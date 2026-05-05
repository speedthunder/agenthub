from agent_platform.app import app
from fastapi.responses import RedirectResponse


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")
