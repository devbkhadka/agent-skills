# Browser Session Management and State Persistence

Use this workflow to preserve authentication states across runs, manage cookies, manipulate localStorage / sessionStorage, and run multiple isolated browser sessions in parallel.

---

## 1. Authentication State Persistence (`state-save` & `state-load`)

Avoid repeatedly logging into applications during development and testing by saving and reusing authentication state.

### Step 1: Log in Once & Save State
```bash
# Open login page and complete login flow
playwright-cli open https://app.example.com/login
playwright-cli fill e1 "testuser@example.com"
playwright-cli fill e2 "SecurePassword123"
playwright-cli click e3

# Save authenticated cookies and storage to file
playwright-cli state-save .playwright-cli/auth-state.json
playwright-cli close
```

### Step 2: Restore State in Future Sessions
```bash
# Load state into new session
playwright-cli state-load .playwright-cli/auth-state.json

# Navigate directly to protected pages without logging in
playwright-cli open https://app.example.com/dashboard
```

> [!CAUTION]
> Never commit authentication state files containing real tokens or session cookies. Always add `.playwright-cli/*.json` or `*auth-state*.json` to `.gitignore`.

---

## 2. Cookie Management

```bash
# List all cookies (optionally filter by domain or path)
playwright-cli cookie-list
playwright-cli cookie-list --domain=example.com

# Inspect specific cookie
playwright-cli cookie-get session_token

# Set a cookie with flags
playwright-cli cookie-set session_token "xyz789" --domain=example.com --httpOnly --secure --sameSite=Lax

# Delete or clear cookies
playwright-cli cookie-delete session_token
playwright-cli cookie-clear
```

---

## 3. LocalStorage & SessionStorage

```bash
# LocalStorage CRUD
playwright-cli localstorage-list
playwright-cli localstorage-get authToken
playwright-cli localstorage-set userTheme "dark"
playwright-cli localstorage-set appConfig '{"onboardingComplete":true,"flags":{"beta":true}}'
playwright-cli localstorage-delete userTheme
playwright-cli localstorage-clear

# SessionStorage CRUD
playwright-cli sessionstorage-list
playwright-cli sessionstorage-get currentWizardStep
playwright-cli sessionstorage-set currentWizardStep "3"
playwright-cli sessionstorage-clear
```

---

## 4. Multi-Session Isolation (`-s=<name>`)

Run concurrent browser instances with completely isolated cookies, caches, storage, and browsing histories:

```bash
# Session 1: Admin user
playwright-cli -s=admin open https://app.example.com/admin
playwright-cli -s=admin fill e1 "admin@example.com"

# Session 2: Regular customer (isolated storage)
playwright-cli -s=customer open https://app.example.com/store
playwright-cli -s=customer fill e1 "customer@example.com"

# List active sessions
playwright-cli list

# Close individual or all sessions
playwright-cli -s=admin close
playwright-cli close-all

# Terminate zombie processes if necessary
playwright-cli kill-all
```

---

## 5. Persistent Browser Profiles

By default, `playwright-cli` sessions run in-memory. To persist state automatically across browser restarts:

```bash
# Launch with persistent profile in auto-generated directory
playwright-cli open https://example.com --persistent

# Launch with custom profile directory
playwright-cli open https://example.com --profile=/path/to/profile-dir

# Wipe profile data
playwright-cli delete-data
```
