# Launch & Attach Configurations (`launch.json`)

Configure Antigravity IDE / VS Code to attach to the Chromium browser instance launched by Playwright with `--remote-debugging-port=9222`.

---

## 1. Standard Attach Configuration

Create or update `.vscode/launch.json` in your workspace:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Attach to Browser App",
      "type": "chrome",
      "request": "attach",
      "port": 9222,
      "webRoot": "${workspaceFolder}",
      "sourceMaps": true,
      "timeout": 30000,
      "trace": false
    }
  ]
}
```

---

## 2. Framework-Specific Source Mapping

Different frontend bundlers place source maps at slightly different root paths:

### Vite (React / Vue / Svelte)
```json
{
  "name": "Attach to Vite App",
  "type": "chrome",
  "request": "attach",
  "port": 9222,
  "webRoot": "${workspaceFolder}",
  "sourceMaps": true,
  "sourceMapPathOverrides": {
    "/@fs/*": "/*",
    "/src/*": "${workspaceFolder}/src/*"
  }
}
```

### Next.js (App Router & Pages Router)
Ensure `next.config.js` has productionBrowserSourceMaps enabled during dev testing if needed:
```json
{
  "name": "Attach to Next.js Client",
  "type": "chrome",
  "request": "attach",
  "port": 9222,
  "webRoot": "${workspaceFolder}",
  "sourceMaps": true,
  "sourceMapPathOverrides": {
    "webpack://_N_E/*": "${workspaceFolder}/*"
  }
}
```

### Monorepos (Turborepo / Nx / pnpm workspaces)
```json
{
  "name": "Attach to Monorepo App",
  "type": "chrome",
  "request": "attach",
  "port": 9222,
  "webRoot": "${workspaceFolder}/apps/web",
  "sourceMaps": true,
  "sourceMapPathOverrides": {
    "webpack:///./*": "${webRoot}/*",
    "webpack:///../*": "${workspaceFolder}/*"
  }
}
```

---

## 3. Verifying Sourcemaps

To guarantee that breakpoints bind cleanly (turning bright red rather than grey/unbound):
1. Ensure your bundler generates sourcemaps (`sourcemap: true` in `vite.config.ts`, `devtool: 'source-map'` in `webpack.config.js`).
2. Verify `webRoot` points to the folder containing your `src/` directory.
