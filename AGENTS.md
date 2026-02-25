# Agent Rules

## Code Quality
- Write clean, readable code with meaningful comments where logic isn't obvious
- Use descriptive variable and function names — no single letters or abbreviations
- Add type hints to all function signatures
- Keep files under 300 lines — split into modules when they grow

## Architecture
- Separate concerns into different files (routes, helpers, config, storage, etc.)
- Don't duplicate logic — extract shared code into helper functions
- If a function or pattern already exists in the codebase, reuse it instead of writing a new one
- Check for existing utilities before creating new ones

## Changes
- Only modify what was asked — don't refactor surrounding code unless requested
- Don't remove existing code unless explicitly asked
- Don't rename variables or functions that aren't part of the task
- Explain what you're changing and why before making edits
- Place new code logically near related existing code

## Error Handling
- Handle all errors with try/catch or appropriate error responses
- Return meaningful error messages — not generic "something went wrong"
- Use proper HTTP status codes (404, 400, 500)

## Security
- Use environment variables for all secrets and API keys — never hardcode them
- Don't expose internal paths or stack traces in API responses
- Validate user input at API boundaries

## Dependencies
- Update requirements.txt when adding new Python packages
- Update package.json when adding new Node packages
- Prefer lightweight, well-maintained libraries

## Testing
- After making changes, verify your work by running the relevant code or calling the endpoint
- Use `curl` to test API endpoints (the server runs on port 8000)
- Don't start or restart the server — assume it's already running
- Don't run long-running processes or background servers
- If a port is in use, tell the user instead of trying random ports
- If a test fails, fix the issue before finishing

## Logging
- Add logging for important operations and error cases
- Use Python's logging module, not print statements

## Git
- Don't commit or push unless explicitly asked
- Don't modify .env files
