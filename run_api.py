import uvicorn

if __name__ == "__main__":
    print("Starting YouTube Outlier Tracker API...")
    print("Keep this terminal open while browsing YouTube.")
    print("API docs: http://localhost:8000/docs")
    print("Press Ctrl+C to stop.\n")
    uvicorn.run("api.server:app", host="127.0.0.1", port=8000, reload=False)