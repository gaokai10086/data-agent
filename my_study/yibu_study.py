def modify(data):
    data["count"] = 10
    data = {"count": 20}
    return data

state = {"count": 0}

result = modify(state)

print(state["count"])
print(result["count"])