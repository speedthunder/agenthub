from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from agent_platform.app import app as agenthub_app

main_app = FastAPI()

# 將子應用掛載到 /agenthub
main_app.mount("/agenthub", agenthub_app)

@main_app.get("/agenthub")
def agenthub_root():
    return RedirectResponse(url="/agenthub/static/index.html")

@main_app.get("/")
def root():
    return RedirectResponse(url="/agenthub/static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent_platform.main:main_app", host="0.0.0.0", port=8000, reload=True)
