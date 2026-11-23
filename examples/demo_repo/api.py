from fastapi import FastAPI

app = FastAPI()


class Inventory:
    def items(self):
        return []


@app.get("/items")
async def list_items():
    return Inventory().items()


@app.post("/items")
def add_item():
    return {"created": True}
