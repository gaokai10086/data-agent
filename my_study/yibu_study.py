data = {
    "current_sales": 80000,
    "previous_sales": 100000
}


def calculate_change(data: dict) -> dict:
    #  本期销售额
    current_sales = data.get('current_sales')
    #  上期销售额
    previous_sales = data.get('previous_sales')

    try:
        change_rate = (current_sales - previous_sales) / previous_sales * 100
        if change_rate > 0 :
            return {change_rate,"increase"}
        elif change_rate < 0 :
            return {change_rate,"decrease"}
        else:
            return {change_rate,"unchanged"}
    except Exception as e:
        return  {"error":"上期销售额不能为0"}

print(calculate_change(data))