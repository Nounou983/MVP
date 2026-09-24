# Static frontend deployment

The project frontend is already a static application: no Node build is required.

For a Hugging Face Static Space, use the project root as the Space repository and
set the Space `app_file` to `index.html`. Before publishing, edit
`deployment-config.js` so `apiBase` and `aiApi` point to the deployed API/AI
services.

Do not put secrets in `deployment-config.js`.
