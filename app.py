import part1
import part2
import part3
import part4  # <--- ДОБАВИТЬ ЭТО
import uvicorn

from part1 import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)