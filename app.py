import uvicorn
from main import app
import api_routes

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
