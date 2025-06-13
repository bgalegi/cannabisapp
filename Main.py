from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import sqlite3
import requests
import json
from datetime import datetime
import logging
import uvicorn

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Logging for compliance
logging.basicConfig(filename='cannabis_movement.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# METRC credentials (to be added later)
METRC_API_KEY = ""  # Add your API key here
METRC_BASE_URL = "https://api-ma.metrc.com"
FACILITY_LICENSE = ""  # Add your license number here

# Initialize database
def init_db():
    conn = sqlite3.connect('cannabis_inventory.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS packages (
                    tag_id TEXT PRIMARY KEY,
                    location TEXT,
                    last_updated TIMESTAMP,
                    status TEXT
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag_id TEXT,
                    from_location TEXT,
                    to_location TEXT,
                    timestamp TIMESTAMP,
                    metrc_synced BOOLEAN
                 )''')
    conn.commit()
    conn.close()

# WebSocket for real-time updates
connected_clients = set()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        connected_clients.remove(websocket)

async def broadcast(data: dict):
    for client in connected_clients:
        await client.send_json(data)

# Move package
@app.post("/move_package")
async def move_package(data: dict):
    tag_id = data.get('tag_id')
    from_location = data.get('from_location')
    to_location = data.get('to_location')
    
    if not tag_id or not from_location or not to_location:
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    try:
        conn = sqlite3.connect('cannabis_inventory.db')
        c = conn.cursor()
        
        # Verify package
        c.execute("SELECT location FROM packages WHERE tag_id = ?", (tag_id,))
        result = c.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail=f"Package {tag_id} not found")
        if result[0] != from_location:
            raise HTTPException(status_code=400, detail=f"Package {tag_id} is in {result[0]}, not {from_location}")
        
        # Update package
        timestamp = datetime.now().isoformat()
        c.execute("UPDATE packages SET location = ?, last_updated = ?, status = ? WHERE tag_id = ?",
                  (to_location, timestamp, "Active", tag_id))
        
        # Log movement
        c.execute("INSERT INTO movements (tag_id, from_location, to_location, timestamp, metrc_synced) VALUES (?, ?, ?, ?, ?)",
                  (tag_id, from_location, to_location, timestamp, False))
        
        conn.commit()
        logging.info(f"Moved package {tag_id} from {from_location} to {to_location}")
        
        # Broadcast update
        await broadcast({
            "tag_id": tag_id,
            "from_location": from_location,
            "to_location": to_location,
            "timestamp": timestamp
        })
        
        # Sync with METRC (disabled until credentials are added)
        if METRC_API_KEY and FACILITY_LICENSE:
            sync_with_metrc(tag_id, to_location, timestamp)
        
        return {"message": "Package moved successfully"}
    
    except Exception as e:
        logging.error(f"Error moving package {tag_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# METRC sync
def sync_with_metrc(tag_id: str, new_location: str, timestamp: str):
    try:
        headers = {"Authorization": f"Bearer {METRC_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "licenseNumber": FACILITY_LICENSE,
            "packageLabel": tag_id,
            "room": new_location,
            "moveDateTime": timestamp
        }
        
        response = requests.post(f"{METRC_BASE_URL}/packages/v1/move", headers=headers, json=[payload])
        
        if response.status_code == 200:
            conn = sqlite3.connect('cannabis_inventory.db')
            c = conn.cursor()
            c.execute("UPDATE movements SET metrc_synced = ? WHERE tag_id = ? AND timestamp = ?",
                      (True, tag_id, timestamp))
            conn.commit()
            conn.close()
            logging.info(f"Synced package {tag_id} to METRC")
        else:
            logging.error(f"METRC sync failed for {tag_id}: {response.text}")
            with open('pending_metrc_sync.json', 'a') as f:
                json.dump(payload, f)
                f.write('\n')
                
    except Exception as e:
        logging.error(f"METRC sync error for {tag_id}: {str(e)}")
        with open('pending_metrc_sync.json', 'a') as f:
            json.dump({"tag_id": tag_id, "new_location": new_location, "timestamp": timestamp}, f)
            f.write('\n')

# Serve PWA
@app.get("/")
async def serve_pwa():
    with open("static/index.html") as f:
        return HTMLResponse(content=f.read())

if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8000)