from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import shutil
import os

app = FastAPI()

# Allow cross-origin requests for development (adjust allow_origins for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # change to ['http://127.0.0.1:5500'] or your frontend origin in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temporary folder to store uploaded files
UPLOAD_DIR = Path("temp_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Mount frontend folder as static files so the backend can serve the UI.
FRONTEND_DIR = Path(__file__).parent / "frontend"
if (FRONTEND_DIR.exists()):
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def serve_frontend():
    """
    Serve the frontend's index.html content when available.
    
    Reads FRONTEND_DIR/index.html and returns its contents as an HTMLResponse with status 200. If the file is missing, returns a JSONResponse with a message indicating the frontend is not found and a 404 status.
    
    Returns:
        HTMLResponse: if index.html exists, containing the file's UTF-8 text and status 200.
        JSONResponse: if index.html is missing, containing an error message and status 404.
    """
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        # Read file and return as HTMLResponse to avoid Content-Length mismatch issues
        text = index_path.read_text(encoding="utf-8")
        return HTMLResponse(content=text, status_code=200)
    return JSONResponse({"message": "Frontend not found. Serve frontend separately."}, status_code=404)

@app.post("/upload/")
async def upload_files(files: list[UploadFile] = File(...)):
    """
    Save uploaded files to the module's UPLOAD_DIR using each file's original filename and return a JSON response listing the saved filenames.
    
    Parameters:
        files (list[UploadFile]): Uploaded files from the multipart form data.
    
    Returns:
        JSONResponse: JSON object with an "uploaded_files" key containing a list of saved filenames.
    """
    saved_files = []
    for file in files:
        file_path = UPLOAD_DIR / file.filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)
    return JSONResponse(content={"uploaded_files": saved_files})