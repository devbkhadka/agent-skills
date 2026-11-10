#!/usr/bin/env python3
"""
Atomic Resource Lock Pool Manager for Agent Herder.

Manages resource locks (files, folders, worktrees, tasks, GPUs, APIs, etc.)
grouped into arbitrary namespaces (default: 'global').

Locks are LEASES, not permanent claims. Every holder records pid/host/expiry so
a crashed agent or a crashed herder can never deadlock the pool forever:

  * TTL      - a lease expires unless renewed; expired leases are reaped.
  * Liveness - same-host holders are probed with os.kill(pid, 0); a dead
               process is reaped immediately, without waiting for the TTL.
  * FIFO     - waiters are granted in arrival order, so a busy resource cannot
               starve an early waiter. Waiters that stop polling are reaped.

State file: ~/.config/.agents/resource_pool/state.json  (schema version 2)
Schema v1 (bare "locked_by" name lists) is migrated transparently on read.
"""

import sys
import os
import json
import time
import socket
import argparse
import fcntl
import re
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / ".config" / ".agents" / "resource_pool"
DEFAULT_STATE_FILE = Path(os.environ.get(
    "AGENT_HERDER_POOL_STATE_FILE", str(DEFAULT_STATE_DIR / "state.json")
))

SCHEMA_VERSION = 2
DEFAULT_TTL = 900.0      # 15 min: an abandoned lease can never be permanent
WAITER_TTL = 30.0        # a waiter that stops polling loses its queue slot
POLL_INTERVAL = 0.5
AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class StateError(RuntimeError):
    pass


def agent_id(value: str) -> str:
    if not AGENT_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(f"agent must match {AGENT_RE.pattern}")
    return value


def scoped_id(value: str) -> str:
    if (not value or len(value) > 512 or value.startswith("-")
            or any(ord(c) < 32 for c in value)):
        raise argparse.ArgumentTypeError(
            "identifier must be non-empty, <=512 characters, contain no controls, and not start '-'"
        )
    return value


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed


def _now() -> float:
    return time.time()


def _host() -> str:
    return socket.gethostname()


def _pid_alive(pid: int) -> bool:
    """True if pid is running. Only meaningful on the recording host."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user.
        return True
    except OSError:
        return True


class ResourcePoolManager:
    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self.state_file = state_file
        self.lock_file = state_file.with_suffix(".lock")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = None

    # ---------------- file locking ----------------

    def _lock(self):
        self._lock_fd = open(self.lock_file, "w")
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)

    def _unlock(self):
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
        except Exception:
            pass

    # ---------------- state io ----------------

    def _empty(self) -> dict:
        return {"version": SCHEMA_VERSION, "groups": {"global": {"resources": {}}}}

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            return self._empty()
        try:
            content = self.state_file.read_text()
            state = json.loads(content)
        except (OSError, json.JSONDecodeError) as e:
            raise StateError(f"cannot read valid resource state {self.state_file}: {e}") from e
        state = self._migrate(state)
        self._validate_state(state)
        return state

    @staticmethod
    def _validate_state(state: dict):
        if not isinstance(state, dict) or state.get("version") != SCHEMA_VERSION:
            raise StateError("resource state has an unsupported schema")
        groups = state.get("groups")
        if not isinstance(groups, dict):
            raise StateError("resource state groups must be an object")
        for group, grp in groups.items():
            if not isinstance(group, str) or not isinstance(grp, dict):
                raise StateError("resource group entries are malformed")
            resources = grp.get("resources")
            if not isinstance(resources, dict):
                raise StateError(f"resource group {group!r} has invalid resources")
            for resource, res in resources.items():
                if not isinstance(resource, str) or not isinstance(res, dict):
                    raise StateError(f"resource entry in {group!r} is malformed")
                capacity = res.get("capacity", 1)
                if type(capacity) is not int or capacity < 1:
                    raise StateError(f"resource {group}/{resource} has invalid capacity")
                if "ttl" in res and (type(res["ttl"]) not in (int, float) or res["ttl"] <= 0):
                    raise StateError(f"resource {group}/{resource} has invalid ttl")
                holders, waiting = res.get("holders"), res.get("waiting")
                if not isinstance(holders, list) or not isinstance(waiting, list):
                    raise StateError(f"resource {group}/{resource} has invalid lease lists")
                if len(holders) > capacity:
                    raise StateError(f"resource {group}/{resource} exceeds its capacity")
                holder_names = []
                for holder in holders:
                    if (not isinstance(holder, dict) or not isinstance(holder.get("agent"), str)
                            or type(holder.get("expires_at")) not in (int, float)):
                        raise StateError(f"resource {group}/{resource} has malformed holder")
                    holder_names.append(holder["agent"])
                waiter_names = []
                for waiter in waiting:
                    if (not isinstance(waiter, dict) or not isinstance(waiter.get("agent"), str)
                            or type(waiter.get("since")) not in (int, float)
                            or type(waiter.get("seen_at")) not in (int, float)):
                        raise StateError(f"resource {group}/{resource} has malformed waiter")
                    waiter_names.append(waiter["agent"])
                if (len(holder_names) != len(set(holder_names))
                        or len(waiter_names) != len(set(waiter_names))
                        or set(holder_names) & set(waiter_names)):
                    raise StateError(f"resource {group}/{resource} has duplicate lease identities")

    def _migrate(self, state: dict) -> dict:
        """Coerce schema v1 (list-of-names) into v2 (list-of-lease-objects)."""
        if not isinstance(state, dict) or not isinstance(state.get("groups", {}), dict):
            raise StateError("resource state root is malformed")
        if state.get("version") == SCHEMA_VERSION:
            return state
        if state.get("version") not in (None, 1):
            raise StateError(f"unsupported resource state version: {state.get('version')!r}")
        now = _now()
        for grp in state.get("groups", {}).values():
            for res in grp.get("resources", {}).values():
                holders = []
                for h in res.pop("locked_by", []) or []:
                    if isinstance(h, dict):
                        holders.append(h)
                    else:
                        # Legacy name-only lock: give it a TTL so it can expire.
                        holders.append({
                            "agent": h, "pid": 0, "host": "",
                            "acquired_at": now, "renewed_at": now,
                            "expires_at": now + DEFAULT_TTL, "migrated": True,
                        })
                res["holders"] = holders
                waiting = []
                for w in res.get("waiting", []) or []:
                    if isinstance(w, dict):
                        waiting.append(w)
                    else:
                        waiting.append({"agent": w, "since": now, "seen_at": now})
                res["waiting"] = waiting
        state["version"] = SCHEMA_VERSION
        return state

    def _save_state(self, state: dict):
        state["version"] = SCHEMA_VERSION
        self._validate_state(state)
        temp_file = self.state_file.with_name(f".{self.state_file.name}.{os.getpid()}.tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(state, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, self.state_file)
            dir_fd = os.open(self.state_file.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as e:
            try:
                temp_file.unlink(missing_ok=True)
            except OSError:
                pass
            raise StateError(f"cannot persist resource state {self.state_file}: {e}") from e

    # ---------------- helpers ----------------

    @staticmethod
    def _res(state: dict, group: str, resource: str, default_capacity: int = 1) -> dict:
        grp = state.setdefault("groups", {}).setdefault(group, {"resources": {}})
        return grp.setdefault("resources", {}).setdefault(
            resource, {"capacity": default_capacity, "holders": [], "waiting": []}
        )

    @staticmethod
    def _reap_resource(res: dict, now: float) -> list:
        """Drop expired/dead holders and stale waiters. Returns reaped records."""
        reaped = []
        live = []
        for h in res.get("holders", []) or []:
            expired = h.get("expires_at", 0) <= now
            # Only trust pid liveness when the lease was taken on this host.
            dead = (
                h.get("host") == _host()
                and h.get("pid")
                and not _pid_alive(h["pid"])
            )
            if expired or dead:
                reaped.append({**h, "reason": "expired" if expired else "process_gone"})
            else:
                live.append(h)
        res["holders"] = live

        res["waiting"] = [
            w for w in (res.get("waiting", []) or [])
            if w.get("seen_at", 0) + WAITER_TTL > now
        ]
        return reaped

    def _reap_all(self, state: dict, group: str = None) -> list:
        now = _now()
        out = []
        for gname, grp in state.get("groups", {}).items():
            if group and gname != group:
                continue
            for rname, res in grp.get("resources", {}).items():
                for r in self._reap_resource(res, now):
                    out.append({"group": gname, "resource": rname, **r})
        return out

    # ---------------- commands ----------------

    def define(self, group: str, resource: str, capacity: int, ttl: float = None) -> dict:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be at least 1")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be greater than zero")
        self._lock()
        try:
            state = self._load_state()
            res = self._res(state, group, resource, capacity)
            res["capacity"] = capacity
            if ttl is not None:
                res["ttl"] = ttl
            self._save_state(state)
            return {
                "status": "ok", "group": group, "resource": resource,
                "capacity": capacity, "ttl": res.get("ttl", DEFAULT_TTL),
            }
        finally:
            self._unlock()

    def acquire(self, group: str, resource: str, agent: str, timeout: float = 0,
                default_capacity: int = 1, ttl: float = None, pid: int = None,
                fifo: bool = True) -> dict:
        if timeout < 0 or default_capacity < 1 or (ttl is not None and ttl <= 0):
            raise ValueError("invalid timeout, capacity, or ttl")
        start = _now()
        # pid 0 == "no liveness claim". The CLI process is short-lived, so its
        # own pid is meaningless here; only the caller knows which pid actually
        # represents the agent's lifetime. Crash detection is opt-in via --pid.
        pid = pid if pid is not None else 0
        reaped_total = []

        while True:
            self._lock()
            try:
                now = _now()
                state = self._load_state()
                res = self._res(state, group, resource, default_capacity)
                reaped_total += self._reap_resource(res, now)

                lease_ttl = ttl if ttl is not None else res.get("ttl", DEFAULT_TTL)
                capacity = res.get("capacity", default_capacity)
                holders = res["holders"]

                # Already holding -> renew rather than double-count.
                for h in holders:
                    if h["agent"] == agent:
                        h["renewed_at"] = now
                        h["expires_at"] = now + lease_ttl
                        h["pid"] = pid
                        h["host"] = _host()
                        res["waiting"] = [w for w in res["waiting"] if w["agent"] != agent]
                        self._save_state(state)
                        return {
                            "status": "acquired", "renewed": True, "group": group,
                            "resource": resource, "agent": agent,
                            "expires_at": h["expires_at"],
                            "held": len(holders), "capacity": capacity,
                            "reaped": reaped_total,
                        }

                # Track our place in the queue (fixed `since`, refreshed `seen_at`).
                waiting = res.setdefault("waiting", [])
                mine = next((w for w in waiting if w["agent"] == agent), None)
                if mine is None:
                    mine = {"agent": agent, "since": now, "seen_at": now}
                    waiting.append(mine)
                else:
                    mine["seen_at"] = now

                free = capacity - len(holders)
                # FIFO: only claim a slot if enough are free to cover everyone
                # who has been waiting longer than us.
                ahead = sum(
                    1 for w in waiting
                    if w["agent"] != agent
                    and (w["since"], w["agent"]) < (mine["since"], mine["agent"])
                )
                may_take = free > 0 and (not fifo or ahead < free)

                if may_take:
                    holders.append({
                        "agent": agent, "pid": pid, "host": _host(),
                        "acquired_at": now, "renewed_at": now,
                        "expires_at": now + lease_ttl,
                    })
                    res["waiting"] = [w for w in waiting if w["agent"] != agent]
                    self._save_state(state)
                    return {
                        "status": "acquired", "renewed": False, "group": group,
                        "resource": resource, "agent": agent,
                        "expires_at": now + lease_ttl,
                        "held": len(holders), "capacity": capacity,
                        "reaped": reaped_total,
                    }

                self._save_state(state)
                blocked = {
                    "status": "locked", "error": "Resource limit reached",
                    "group": group, "resource": resource, "agent": agent,
                    "held": len(holders), "capacity": capacity,
                    "queue_position": ahead + 1,
                    "holders": [h["agent"] for h in holders],
                    "reaped": reaped_total,
                }
            finally:
                self._unlock()

            if timeout <= 0 or (_now() - start) >= timeout:
                return blocked
            time.sleep(POLL_INTERVAL)

    def renew(self, group: str, resource: str, agent: str, ttl: float = None) -> dict:
        """Extend a held lease. Fails if the lease was already reaped -- the
        agent MUST treat failure as 'I no longer hold this resource'."""
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be greater than zero")
        self._lock()
        try:
            now = _now()
            state = self._load_state()
            res = self._res(state, group, resource)
            self._reap_resource(res, now)
            lease_ttl = ttl if ttl is not None else res.get("ttl", DEFAULT_TTL)
            for h in res["holders"]:
                if h["agent"] == agent:
                    h["renewed_at"] = now
                    h["expires_at"] = now + lease_ttl
                    self._save_state(state)
                    return {
                        "status": "renewed", "group": group, "resource": resource,
                        "agent": agent, "expires_at": h["expires_at"],
                    }
            self._save_state(state)
            return {
                "status": "lost", "error": "Lease not held (expired or released)",
                "group": group, "resource": resource, "agent": agent,
            }
        finally:
            self._unlock()

    def release(self, group: str, resource: str, agent: str) -> dict:
        self._lock()
        try:
            state = self._load_state()
            grp = state.get("groups", {}).get(group, {})
            res = grp.get("resources", {}).get(resource)
            if not res:
                return {"status": "not_found", "group": group,
                        "resource": resource, "agent": agent}

            before_h = len(res.get("holders", []) or [])
            before_w = len(res.get("waiting", []) or [])
            res["holders"] = [h for h in (res.get("holders", []) or [])
                              if h.get("agent") != agent]
            res["waiting"] = [w for w in (res.get("waiting", []) or [])
                              if w.get("agent") != agent]
            dropped_hold = before_h - len(res["holders"])
            dropped_wait = before_w - len(res["waiting"])

            # Persist whenever ANYTHING changed -- including a waiting-only
            # removal, which the previous implementation silently discarded.
            if dropped_hold or dropped_wait:
                self._save_state(state)

            if dropped_hold:
                status = "released"
            elif dropped_wait:
                status = "dequeued"
            else:
                status = "not_found"
            return {"status": status, "group": group, "resource": resource,
                    "agent": agent}
        finally:
            self._unlock()

    def reap(self, group: str = None, dry_run: bool = False) -> dict:
        """Reclaim leases from crashed agents / expired holders."""
        self._lock()
        try:
            state = self._load_state()
            if dry_run:
                # Evaluate against a throwaway copy.
                probe = json.loads(json.dumps(state))
                reaped = self._reap_all(probe, group)
            else:
                reaped = self._reap_all(state, group)
                if reaped:
                    self._save_state(state)
            return {"status": "ok", "dry_run": dry_run,
                    "reaped_count": len(reaped), "reaped": reaped}
        finally:
            self._unlock()

    def status(self, group: str = None, reap: bool = True) -> dict:
        self._lock()
        try:
            state = self._load_state()
            if reap:
                if self._reap_all(state, group):
                    self._save_state(state)
            groups = state.get("groups", {})
            now = _now()
            out = {}
            for gname, grp in groups.items():
                if group and gname != group:
                    continue
                resources = {}
                for rname, res in grp.get("resources", {}).items():
                    resources[rname] = {
                        "capacity": res.get("capacity", 1),
                        "held": len(res.get("holders", []) or []),
                        "holders": [
                            {"agent": h["agent"], "pid": h.get("pid"),
                             "expires_in": round(h.get("expires_at", 0) - now, 1)}
                            for h in (res.get("holders", []) or [])
                        ],
                        "waiting": [w["agent"] for w in (res.get("waiting", []) or [])],
                    }
                out[gname] = {"resources": resources}
            if group and group not in out:
                out[group] = {"resources": {}}
            return out
        finally:
            self._unlock()

    def list_groups(self) -> list:
        self._lock()
        try:
            return list(self._load_state().get("groups", {}).keys())
        finally:
            self._unlock()

    def clear_group(self, group: str) -> dict:
        self._lock()
        try:
            state = self._load_state()
            groups = state.get("groups", {})
            if group in groups:
                del groups[group]
                self._save_state(state)
                return {"status": "cleared", "group": group}
            return {"status": "not_found", "group": group}
        finally:
            self._unlock()

    def release_agent(self, group: str, agent: str) -> dict:
        """Drop every lease/queue entry an agent holds in a group.

        Called when parking an agent for human escalation, or when tearing a
        crashed agent down -- a parked agent must never keep the fleet blocked.
        """
        self._lock()
        try:
            state = self._load_state()
            grp = state.get("groups", {}).get(group, {})
            freed = []
            for rname, res in grp.get("resources", {}).items():
                h0 = len(res.get("holders", []) or [])
                w0 = len(res.get("waiting", []) or [])
                res["holders"] = [h for h in (res.get("holders", []) or [])
                                  if h.get("agent") != agent]
                res["waiting"] = [w for w in (res.get("waiting", []) or [])
                                  if w.get("agent") != agent]
                if h0 != len(res["holders"]) or w0 != len(res["waiting"]):
                    freed.append(rname)
            if freed:
                self._save_state(state)
            return {"status": "ok", "group": group, "agent": agent, "freed": freed}
        finally:
            self._unlock()


def main():
    parser = argparse.ArgumentParser(description="Agent Herder Resource Pool Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--resource", required=True, type=scoped_id)
        p.add_argument("--agent", required=True, type=agent_id)
        p.add_argument("--group", default="global", type=scoped_id)

    p = sub.add_parser("define", help="Define or update a resource capacity")
    p.add_argument("--resource", required=True, type=scoped_id)
    p.add_argument("--capacity", type=positive_int, required=True)
    p.add_argument("--group", default="global", type=scoped_id)
    p.add_argument("--ttl", type=positive_float, default=None,
                   help=f"Lease TTL seconds for this resource (default {DEFAULT_TTL})")

    p = sub.add_parser("acquire", help="Acquire a lease on a resource")
    add_common(p)
    p.add_argument("--timeout", type=nonnegative_float, default=0,
                   help="Max wait seconds (default 0 = non-blocking)")
    p.add_argument("--default-capacity", type=positive_int, default=1)
    p.add_argument("--ttl", type=positive_float, default=None)
    p.add_argument("--pid", type=positive_int, default=None,
                   help="Long-lived PID of the AGENT process (e.g. the pane's "
                        "shell). Enables instant crash detection. Omit to rely "
                        "on TTL alone -- never pass the wrapper's own pid.")
    p.add_argument("--no-fifo", action="store_true",
                   help="Disable fair queueing (allow barging)")

    p = sub.add_parser("renew", help="Extend a held lease (heartbeat)")
    add_common(p)
    p.add_argument("--ttl", type=positive_float, default=None)

    p = sub.add_parser("release", help="Release a lease or queue slot")
    add_common(p)

    p = sub.add_parser("release-agent", help="Release everything an agent holds in a group")
    p.add_argument("--agent", required=True, type=agent_id)
    p.add_argument("--group", default="global", type=scoped_id)

    p = sub.add_parser("reap", help="Reclaim expired / crashed-agent leases")
    p.add_argument("--group", default=None, type=scoped_id)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("status", help="Show pool status")
    p.add_argument("--group", default=None, type=scoped_id)
    p.add_argument("--no-reap", action="store_true")

    sub.add_parser("list-groups", help="List all active resource groups")

    p = sub.add_parser("clear-group", help="Remove an entire resource group")
    p.add_argument("--group", required=True, type=scoped_id)

    args = parser.parse_args()
    mgr = ResourcePoolManager()

    try:
        if args.command == "define":
            print(json.dumps(mgr.define(args.group, args.resource, args.capacity, args.ttl), indent=2))

        elif args.command == "acquire":
            res = mgr.acquire(args.group, args.resource, args.agent, args.timeout,
                              args.default_capacity, args.ttl, args.pid,
                              fifo=not args.no_fifo)
            print(json.dumps(res))
            sys.exit(0 if res["status"] == "acquired" else 1)

        elif args.command == "renew":
            res = mgr.renew(args.group, args.resource, args.agent, args.ttl)
            print(json.dumps(res))
            sys.exit(0 if res["status"] == "renewed" else 1)

        elif args.command == "release":
            res = mgr.release(args.group, args.resource, args.agent)
            print(json.dumps(res))

        elif args.command == "release-agent":
            print(json.dumps(mgr.release_agent(args.group, args.agent)))

        elif args.command == "reap":
            res = mgr.reap(args.group, args.dry_run)
            print(json.dumps(res, indent=2))

        elif args.command == "status":
            print(json.dumps(mgr.status(args.group, reap=not args.no_reap), indent=2))

        elif args.command == "list-groups":
            print(json.dumps(mgr.list_groups(), indent=2))

        elif args.command == "clear-group":
            print(json.dumps(mgr.clear_group(args.group)))
    except (StateError, ValueError, OSError) as e:
        print(json.dumps({"status": "error", "error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
