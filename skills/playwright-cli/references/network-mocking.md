# Network Mocking, Routing, and Traffic Inspection

Use this workflow to mock API backends, intercept network requests, inspect headers and payloads, test error handling states, and simulate offline or slow network conditions with `playwright-cli`.

---

## 1. CLI Route Commands

### Basic Route Mocking
Intercept requests and return stubbed responses directly from the CLI:

```bash
# Mock an API endpoint with custom JSON body
playwright-cli route "**/api/v1/user" --body='{"id": 42, "name": "Jane Doe", "role": "admin"}' --content-type=application/json

# Mock error status codes (e.g. 404 Not Found, 500 Server Error)
playwright-cli route "**/*.jpg" --status=404
playwright-cli route "**/api/checkout" --status=500 --body='{"error": "Payment service unavailable"}'

# Inject custom response headers
playwright-cli route "**/api/data" --body='{"status": "ok"}' --header="X-Custom-Header: verified"

# Strip sensitive or caching headers from outgoing requests
playwright-cli route "**/*" --remove-header=cookie,authorization
```

### Route Management

```bash
# List all currently active route intercepts
playwright-cli route-list

# Remove a specific route pattern
playwright-cli unroute "**/api/v1/user"

# Clear all active routes
playwright-cli unroute
```

---

## 2. URL Matching Patterns

| Pattern | Matches |
| :--- | :--- |
| `**/api/users` | Exact endpoint path on any host |
| `**/api/*/details` | Wildcard path segment |
| `**/*.{png,jpg,webp}` | Specific file extensions / assets |
| `**/search?q=*` | Query parameter matching |

---

## 3. Advanced Mocking via `run-code`

For complex scenarios like conditionally mocking based on payload, altering real backend responses, or simulating network delays:

### Conditional Mocking Based on Request Body
```bash
playwright-cli run-code "async page => {
  await page.route('**/api/auth/login', async route => {
    const postData = route.request().postDataJSON();
    if (postData?.email === 'admin@example.com') {
      await route.fulfill({ status: 200, json: { token: 'mocked-jwt-token' } });
    } else {
      await route.fulfill({ status: 401, json: { error: 'Invalid credentials' } });
    }
  });
}"
```

### Modifying Real API Responses (Interception)
```bash
playwright-cli run-code "async page => {
  await page.route('**/api/profile', async route => {
    const response = await route.fetch();
    const json = await response.json();
    json.flags = { betaUser: true, proTier: true }; // Patch real response
    await route.fulfill({ response, json });
  });
}"
```

### Simulating Network Drops and Delays
```bash
# Simulate network disconnection (internetdisconnected, timedout, connectionreset)
playwright-cli run-code "async page => {
  await page.route('**/api/sync', route => route.abort('internetdisconnected'));
}"

# Simulate latency
playwright-cli run-code "async page => {
  await page.route('**/api/slow', async route => {
    await new Promise(res => setTimeout(res, 2500));
    await route.fulfill({ json: { delayed: true } });
  });
}"
```

---

## 4. Inspecting Network Activity

```bash
# List all network requests made since page load
playwright-cli requests

# View full details (headers, body, response) for request #4
playwright-cli request 4

# View only request headers / payload body
playwright-cli request-headers 4
playwright-cli request-body 4

# View response headers / body
playwright-cli response-headers 4
playwright-cli response-body 4

# Toggle browser offline state
playwright-cli network-state-set offline
playwright-cli network-state-set online
```
