from fastapi import FastAPI

app = FastAPI()

@app.get("/run")
async def run():
    return "Running!"

@app.get("/runs")
async def runs():
    return "List of runs!"

@app.get("/issues")
async def issues():
    return "List of issues!"

@app.get("/analyses")
async def analyses():
    return "List of analyses!"

@app.get("/notifications")
async def notifications():
    return "List of notifications!"
