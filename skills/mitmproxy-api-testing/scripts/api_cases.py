"""Small, opt-in HTTP fault injector. Load with mitmdump -s api_cases.py."""

import asyncio
import json
import logging
import time

from mitmproxy import ctx, exceptions, http


class ApiCases:
    def __init__(self):
        self.hits = 0

    def load(self, loader):
        for name, kind, default, help_text in [
            ("case", str, "pass", "pass, error, delay, timeout, or kill"),
            ("case_host", str, "", "Exact hostname; empty disables matching"),
            ("case_method", str, "POST", "Exact HTTP method"),
            ("case_path", str, "/", "Exact path, excluding query string"),
            ("case_target", str, "", "Optional exact X-Amz-Target"),
            ("case_field", str, "", "Optional top-level JSON field to match"),
            ("case_value", str, "", "Exact string value for case_field"),
            ("case_limit", int, 1, "Maximum injections; 0 means every match"),
            ("case_seconds", float, 60.0, "Delay or timeout hold duration"),
            ("case_status", int, 503, "Injected HTTP status"),
            ("case_error", str, "", "Optional AWS JSON error name"),
            ("case_message", str, "Synthetic test failure", "Error message"),
            ("case_body", str, "", "Optional complete JSON response body"),
        ]:
            loader.add_option(name, kind, default, help_text)

    def configure(self, updates):
        if ctx.options.case not in {"pass", "error", "delay", "timeout", "kill"}:
            raise exceptions.OptionsError("Unknown case")
        if ctx.options.case_limit < 0 or ctx.options.case_seconds < 0:
            raise exceptions.OptionsError("Limit and seconds must be nonnegative")
        if not 400 <= ctx.options.case_status <= 599:
            raise exceptions.OptionsError("Error status must be 400..599")
        if ctx.options.case_body:
            try:
                json.loads(ctx.options.case_body)
            except ValueError as error:
                raise exceptions.OptionsError("case_body must be JSON") from error

    def matches(self, flow):
        options = ctx.options
        request = flow.request
        if not options.case_host or (
            request.pretty_host != options.case_host
            or request.method != options.case_method.upper()
            or request.path.split("?", 1)[0] != options.case_path
        ):
            return False
        if options.case_target and request.headers.get("x-amz-target") != options.case_target:
            return False
        if options.case_field:
            try:
                body = json.loads(request.get_text())
            except (ValueError, TypeError, UnicodeError):
                return False
            if not isinstance(body, dict) or body.get(options.case_field) != options.case_value:
                return False
        return True

    async def request(self, flow: http.HTTPFlow):
        if not self.matches(flow):
            return
        options = ctx.options
        logging.info("api-case matched id=%s case=%s time=%.3f", flow.id, options.case, time.time())
        if options.case == "pass" or (options.case_limit and self.hits >= options.case_limit):
            return
        self.hits += 1  # Reserve before await so concurrent requests respect the limit.
        scenario, seconds = options.case, options.case_seconds
        if scenario in {"delay", "timeout"}:
            await asyncio.sleep(seconds)  # Does not block unrelated traffic.
        if scenario in {"kill", "timeout"}:
            if flow.killable:
                flow.kill()
        elif scenario == "error":
            body = {"message": options.case_message}
            headers = {"content-type": "application/json", "x-test-fault": "synthetic"}
            if options.case_error:
                body["__type"] = options.case_error
                headers.update({"content-type": "application/x-amz-json-1.1",
                                "x-amzn-errortype": options.case_error})
            flow.response = http.Response.make(
                options.case_status, options.case_body or json.dumps(body), headers
            )

    def response(self, flow: http.HTTPFlow):
        if self.matches(flow):
            logging.info("api-case response id=%s status=%s", flow.id, flow.response.status_code)

    def error(self, flow: http.HTTPFlow):
        if self.matches(flow):
            logging.info("api-case transport-error id=%s", flow.id)


addons = [ApiCases()]
