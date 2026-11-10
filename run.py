
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT", "9000"))

    uvicorn.run(
        "src.api:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )
