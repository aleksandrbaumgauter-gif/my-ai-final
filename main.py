import agent_part1
import agent_part2
from agent_part2 import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
