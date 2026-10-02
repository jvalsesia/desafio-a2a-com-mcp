import os
import uvicorn
from server import app

if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", 7301))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
