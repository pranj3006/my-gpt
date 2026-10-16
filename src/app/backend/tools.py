"""
Tools available to the chatbot, including search, data, and stock-order tools.

The stock-order tool uses a LangGraph interrupt so the UI can obtain a human
decision before reporting the simulated order as completed.
"""

import requests
import math
from dotenv import load_dotenv
from langchain_tavily import TavilySearch
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_community.tools.tavily_search import TavilySearchResults
import os
from typing import Literal
from rag import rag_tool
from langgraph.types import interrupt,Command
from db import save_memory, search_memory, list_conversations

load_dotenv(override=True)

# Read optional service credentials once when this module is imported.
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
WEATHERSTACK_API_KEY = os.getenv("WEATHERSTACK_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Configure web search with a small number of detailed results.
search_tool = TavilySearchResults(
    api_key=TAVILY_API_KEY,
    max_results=3,
    topic="general",
    search_depth="advanced")

@tool
def calculator(expression:str)->str:
    """Evaluate a basic arithmetic expression using a restricted set of names.

    The expression can use ``math`` functions, ``abs``, ``min``, ``max``, and
    ``sum``. Built-ins are disabled in the evaluation environment.
    """
    try:
        allowed = {
            "math":math,
            "abs":abs,            
            "min":min,
            "max":max,
            "sum":sum
        }
        result = eval(expression, {"__builtins__":{}}, allowed)
        return str(result)
    except Exception as e:
        return f"Calculation error: {str(e)}"

@tool
def get_stock_price(symbol: str) -> dict:
    """Fetch the latest quote for a ticker from Alpha Vantage.

    Args:
        symbol: Stock ticker such as ``AAPL`` or ``TSLA``.

    Returns:
        The decoded JSON response from Alpha Vantage.
    """
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
    r = requests.get(url)
    return r.json()


@tool
def execute_stock_order(symbol: str, quantity: int, order_type: Literal["buy", "sell"]) -> dict:
    """Pause for human approval, then simulate a stock buy or sell order.

    Args:
        symbol: Ticker to trade.
        quantity: Number of shares requested.
        order_type: Either ``"buy"`` or ``"sell"``.

    Returns:
        A result dictionary with a success, cancelled, or failure status.
    """

    if order_type=="buy":
        # LangGraph suspends this tool here and continues after the UI resumes it.
        decision = interrupt(f"Approve {order_type} , {quantity} shares  of {symbol}? (yes/no)")
        if isinstance(decision,str) and decision.strip().lower()=="yes":
            return {
                "status":"success",
                "message": f"Buy order placed for {quantity} for stock of {symbol}",
                "symbol": symbol,
                "quantity": quantity,
                "order_type": order_type
            }
        else:
            return {
                "status":"cancelled",
                "message": f"Buy order for {quantity} for stock of {symbol} is cancelled by User",
                "symbol": symbol,
                "quantity": quantity,
                "order_type": order_type
            }
    elif order_type=="sell":
        decision = interrupt(f"Approve {order_type} , {quantity} shares  of {symbol}? (yes/no)")
        if isinstance(decision,str) and decision.strip().lower()=="yes":
            return {
                "status":"success",
                "message": f"Sell order placed for {quantity} for stock of {symbol}",
                "symbol": symbol,
                "quantity": quantity,
                "order_type": order_type
            }
        else:
            return {
                "status":"cancelled",
                "message": f"Sell order for {quantity} for stock of {symbol} is cancelled by User",
                "symbol": symbol,
                "quantity": quantity,
                "order_type": order_type
            }
    else:
        return {
            "status":"fail",
            "message": f"Invalid order type: {order_type}",
            "symbol": symbol,
            "quantity": quantity,
            "order_type": order_type
        }


@tool
def get_weather_data(city: str) -> str:
    """Fetch current temperature, humidity, and conditions for a city.

    Raises:
        ValueError: If the Weatherstack API key is not configured.
    """
    if not WEATHERSTACK_API_KEY:
        raise ValueError("WEATHERSTACK_API_KEY is not set in the environment variables.")

    url = f"http://api.weatherstack.com/current?access_key={WEATHERSTACK_API_KEY}&query={city}"
    response = requests.get(url)
    data = response.json()

    if "error" in data:
        return f"Error fetching weather data: {data['error']['info']}"

    if "current" not in data:
        return "Error: No current weather data found."
    current_weather = data.get("current", {})
    temperature = current_weather.get("temperature")
    humidity = current_weather.get("humidity")
    weather_descriptions = current_weather.get("weather_descriptions", [])
    description = weather_descriptions[0] if weather_descriptions else "No description available"

    return {
        "city": city,
        "temperature": temperature,
        "humidity": humidity,
        "description": description
    }


@tool
def remember_this(memory: str, config: RunnableConfig) -> str:
    """
    Save important user preference and facts in long term memory
    Use this when user asks you to remember something
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    thread_id = configurable.get("thread_id")
    if not isinstance(user_id, str) or not user_id or not isinstance(thread_id, str):
        raise ValueError("Memory saving requires an authenticated conversation.")
    return save_memory(user_id=user_id, thread_id=thread_id, memory=memory)

@tool
def recall_memory(query: str, config: RunnableConfig) -> str:
    """
    Recall saved long-term memories about the user or this conversation.
    """

    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    thread_id = configurable.get("thread_id")
    if not isinstance(user_id, str) or not user_id or not isinstance(thread_id, str):
        raise ValueError("Memory recall requires an authenticated conversation.")
    return search_memory(user_id=user_id, thread_id=thread_id, query=query)

# Register tools here so the chatbot model and ToolNode share the same catalog.
tools = [search_tool,
    get_stock_price, 
    get_weather_data,
    calculator,
    rag_tool,
    execute_stock_order,
    remember_this,
    recall_memory]
