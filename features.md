FluidXPro3 is the new version of a personal project of mine which I want to release as open source. The idea of FluidXPro3 is simple. 

When we code in C++, we write a source code. It is then preprocessed, compiled and finally a runnable binary is created.

With the emerging tools of prompt engineering and powerful LLMs, we are trying to make the programming more easier and language agnostic. 

Python is an easier language since it is very close to human understandable language with the ease of object oriented programming and a lot of other utility tools and libraries whereas C is not that easy to learn or understand, neither it has a lot of library support. We want to make a language, that is fluid, that means it does not have strict syntax. It is transcribed using an LLm to an actual programming language which is then compiled to binary code.

We just try to make programming easy for everyone who has the logic in their mind. Just write the logic/construct/architecture and fluidXPro3 will convert it to actual production grade code. An example can be like this:

```python
from fastapi import FastAPI
from typing import Union

# Initialize the FastAPI application
app = FastAPI(title="Sample FastAPI Server")

# 1. A simple GET endpoint
@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

# 2. An endpoint with a path parameter and an optional query parameter
@app.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None):
    return {"item_id": item_id, "query": q}

# 3. A POST endpoint to receive data
@app.post("/items/")
def create_item(name: str, price: float):
    return {"name": name, "price": price, "status": "created"}
```

using fluidXPro3, this can be like:

```
s = Server()

s.endpoints({
    "/" : {
        Action: Return a dummy JSON response
    },
    "/items/{id}" : {
        Action: Return JSON response with `item_id` and `query`
    },
    "/items/" : {
        Input: name, price
        Action: return JSON response with `name`, `price` and `status`=`created`
    }
})

s.keepRunning()
```

Now, someone else may explain the same thing like this:

```
s = Action("Create a server")

s.endpoint("/").behavior(Action("Return a dummy JSON"))
s.endpoint("/items/{item-id}").behavior(Action("Return JSON response with `item_id` and `query`"))
s.endpoint("/items/").expectInput("name", "price").behavior("return JSON response with `name`, `price` and `status`=`created`")
```

In both the cases it would return the same kind of code when generated. 