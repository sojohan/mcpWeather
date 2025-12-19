import requests
from datetime import datetime, timedelta, timezone
from mcp.server import Server
from mcp.types import Tool, TextContent
import json
import asyncio
import sys
import os

# Initialize MCP server
server = Server("weather")

# API endpoint
URL = "https://opendataapi.dmi.dk/v1/forecastedr/collections/harmonie_dini_sf/position"

def get_weather_condition(cloud_cover, precipitation, temperature_celsius=None, hour=None):
    """
    Determine weather condition based on cloud cover, precipitation, temperature, and time.
    
    Cloud cover in oktas (0-8):
    - 0: Clear/Sunny
    - 1-2: Mostly clear
    - 3-4: Partly cloudy
    - 5-6: Mostly cloudy
    - 7-8: Overcast
    
    Precipitation in mm:
    - 0: No rain
    - 0.1-2: Light rain
    - 2.1-10: Moderate rain
    - >10: Heavy rain
    
    If cloud/precipitation data unavailable, use temperature for basic estimate.
    """
    # Check for precipitation first (most important)
    if precipitation is not None and precipitation > 0.1:
        if precipitation < 2.1:
            rain_desc = "Light rain"
        elif precipitation < 10:
            rain_desc = "Moderate rain"
        else:
            rain_desc = "Heavy rain"
    else:
        rain_desc = None
    
    # Determine cloud condition from actual data
    if cloud_cover is not None:
        if cloud_cover == 0:
            cloud_desc = "Sunny" if not rain_desc else "Clear"
        elif cloud_cover <= 2:
            cloud_desc = "Mostly clear"
        elif cloud_cover <= 4:
            cloud_desc = "Partly cloudy"
        elif cloud_cover <= 6:
            cloud_desc = "Mostly cloudy"
        else:
            cloud_desc = "Overcast"
        
        # Combine cloud and rain conditions
        if rain_desc:
            return f"{cloud_desc}, {rain_desc}"
        else:
            return cloud_desc
    
    # If no cloud cover data but we have precipitation
    if rain_desc:
        return rain_desc
    
    # Fallback: Use temperature-based estimate when no cloud/precipitation data available
    # Note: This is a simplified heuristic since API doesn't provide cloud/precip data
    if temperature_celsius is not None:
        if temperature_celsius > 20:
            return "Warm (likely clear)"
        elif temperature_celsius > 15:
            return "Mild (likely partly cloudy)"
        elif temperature_celsius > 10:
            return "Cool (likely cloudy)"
        elif temperature_celsius > 5:
            return "Cold (likely overcast)"
        else:
            return "Very cold (likely overcast)"
    
    return "N/A"


def get_weather_forecast_impl(
    latitude: float = 55.6761,
    longitude: float = 12.5683,
    days_ahead: int = 3
) -> dict:
    """
    Get weather forecast from DMI API for a specific location.
    
    Args:
        latitude: Latitude of the location (default: 55.6761 for Copenhagen)
        longitude: Longitude of the location (default: 12.5683 for Copenhagen)
        days_ahead: Number of days to forecast ahead (default: 3, max recommended: 5)
    
    Returns:
        Dictionary containing forecast data with:
        - location: Location info (lat, lon)
        - forecast_period: Start and end dates
        - forecast_points: List of forecast data points
        - summary: Summary statistics
    """
    # Forecast time window (now UTC to +days_ahead days)
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days_ahead)
    
    # Try to find valid parameters for cloud cover and precipitation
    test_params_list = [
        "temperature-2m,wind-speed-10m,cloud-cover-low,precipitation-amount",
        "temperature-2m,wind-speed-10m,cloud-cover,precipitation-amount",
        "temperature-2m,wind-speed-10m"
    ]
    
    params = None
    for param_str in test_params_list:
        test_params = {
            "coords": f"POINT({longitude} {latitude})",
            "parameter-name": param_str,
            "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        }
        test_response = requests.get(URL, params=test_params)
        if test_response.status_code == 200:
            params = test_params
            break
    
    if params is None:
        params = {
            "coords": f"POINT({longitude} {latitude})",
            "parameter-name": "temperature-2m,wind-speed-10m",
            "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        }
    
    response = requests.get(URL, params=params)
    
    # Check status and handle errors
    if response.status_code != 200:
        error_msg = f"Error: HTTP {response.status_code}"
        try:
            error_json = response.json()
            error_msg += f" - {error_json}"
        except:
            error_msg += f" - {response.text}"
        return {"error": error_msg}
    
    forecast_json = response.json()
    
    # Parse the forecast data
    domain = forecast_json.get('domain', {})
    axes = domain.get('axes', {})
    time_axis = axes.get('t', {})
    times = time_axis.get('values', [])
    
    ranges = forecast_json.get('ranges', {})
    temp_values = ranges.get('temperature-2m', {}).get('values', [])
    wind_values = ranges.get('wind-speed-10m', {}).get('values', [])
    
    # Try to get cloud cover and precipitation (may not be available)
    cloud_cover_values = []
    precip_values = []
    possible_cloud_params = ['total-cloud-cover', 'cloud-cover-low', 'cloud-cover', 'low-cloud-cover']
    possible_precip_params = ['precipitation', 'precipitation-amount', 'total-precipitation']
    
    for cloud_param in possible_cloud_params:
        if cloud_param in ranges:
            cloud_cover_values = ranges.get(cloud_param, {}).get('values', [])
            break
    
    for precip_param in possible_precip_params:
        if precip_param in ranges:
            precip_values = ranges.get(precip_param, {}).get('values', [])
            break
    
    # Create forecast data points
    forecast_points = []
    for i, time_str in enumerate(times):
        # Parse timestamp
        time_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        
        # Convert temperature from Kelvin to Celsius
        temp_kelvin = temp_values[i] if i < len(temp_values) else None
        temp_celsius = temp_kelvin - 273.15 if temp_kelvin is not None else None
        
        # Wind speed in m/s
        wind_ms = wind_values[i] if i < len(wind_values) else None
        wind_kmh = wind_ms * 3.6 if wind_ms is not None else None
        
        # Cloud cover (in oktas, 0-8)
        cloud_cover = cloud_cover_values[i] if i < len(cloud_cover_values) else None
        
        # Precipitation (in mm)
        precipitation = precip_values[i] if i < len(precip_values) else None
        
        # Get weather condition description
        hour = time_dt.hour
        weather_condition = get_weather_condition(cloud_cover, precipitation, temp_celsius, hour)
        
        point = {
            'datetime': time_dt.strftime('%Y-%m-%d %H:%M'),
            'datetime_iso': time_dt.isoformat(),
            'temperature_celsius': round(temp_celsius, 2) if temp_celsius is not None else None,
            'wind_speed_kmh': round(wind_kmh, 2) if wind_kmh is not None else None,
            'weather_condition': weather_condition
        }
        
        # Only add cloud cover and precipitation if we have data
        if cloud_cover is not None:
            point['cloud_cover_oktas'] = round(cloud_cover, 1)
        if precipitation is not None:
            point['precipitation_mm'] = round(precipitation, 2) if precipitation > 0 else 0.0
        
        forecast_points.append(point)
    
    # Calculate summary statistics
    temps = [p['temperature_celsius'] for p in forecast_points if p['temperature_celsius'] is not None]
    winds = [p['wind_speed_kmh'] for p in forecast_points if p['wind_speed_kmh'] is not None]
    
    summary = {}
    if temps:
        summary['temperature'] = {
            'min': round(min(temps), 2),
            'max': round(max(temps), 2),
            'mean': round(sum(temps) / len(temps), 2)
        }
    if winds:
        summary['wind_speed'] = {
            'min': round(min(winds), 2),
            'max': round(max(winds), 2),
            'mean': round(sum(winds) / len(winds), 2)
        }
    
    return {
        'location': {
            'latitude': latitude,
            'longitude': longitude
        },
        'forecast_period': {
            'start': start.isoformat(),
            'end': end.isoformat(),
            'days_ahead': days_ahead
        },
        'total_points': len(forecast_points),
        'forecast_points': forecast_points,
        'summary': summary,
        'note': 'Cloud/precipitation data may not be available - conditions estimated from temperature when unavailable'
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_weather_forecast",
            description="Get weather forecast from DMI API for a specific location",
            inputSchema={
                "type": "object",
                "properties": {
                    "latitude": {
                        "type": "number",
                        "description": "Latitude of the location (default: 55.6761 for Copenhagen)",
                        "default": 55.6761
                    },
                    "longitude": {
                        "type": "number",
                        "description": "Longitude of the location (default: 12.5683 for Copenhagen)",
                        "default": 12.5683
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days to forecast ahead (default: 3, max recommended: 5)",
                        "default": 3
                    }
                }
            }
        ),
        Tool(
            name="get_weather_forecast_copenhagen",
            description="Get weather forecast for Copenhagen, Denmark",
            inputSchema={
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days to forecast ahead (default: 3, max recommended: 5)",
                        "default": 3
                    }
                }
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "get_weather_forecast":
            latitude = arguments.get("latitude", 55.6761)
            longitude = arguments.get("longitude", 12.5683)
            days_ahead = arguments.get("days_ahead", 3)
            
            result = get_weather_forecast_impl(latitude, longitude, days_ahead)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "get_weather_forecast_copenhagen":
            days_ahead = arguments.get("days_ahead", 3)
            
            result = get_weather_forecast_impl(55.6761, 12.5683, days_ahead)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        else:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    
    except Exception as e:
        import traceback
        error_msg = f"Error calling tool {name}: {str(e)}\n{traceback.format_exc()}"
        return [TextContent(type="text", text=json.dumps({"error": error_msg}))]



async def main():
    """Run the MCP server with SSE transport."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.requests import Request
    from starlette.responses import Response
    import uvicorn
    
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8000))
    
    # Parse optional host and port arguments
    if "--host" in sys.argv:
        host_idx = sys.argv.index("--host")
        if host_idx + 1 < len(sys.argv):
            host = sys.argv[host_idx + 1]
    
    if "--port" in sys.argv:
        port_idx = sys.argv.index("--port")
        if port_idx + 1 < len(sys.argv):
            port = int(sys.argv[port_idx + 1])
    
    print(f"Starting MCP server with SSE transport on {host}:{port}")
    print(f"Access at: http://localhost:{port}")
    print("(Use 'python main_stdio.py' for stdio transport)")
    
    # Debug: Print registered tools
    tool_names = []
    try:
        tools_list = await list_tools()
        tool_names = [tool.name for tool in tools_list]
        print(f"Registered tools: {tool_names}")
    except Exception as e:
        print(f"Warning: Could not list tools: {e}")
    
    # Create SSE transport
    sse_transport = SseServerTransport("/messages/")
    
    # Define root/health check handler
    async def handle_root(request: Request):
        from starlette.responses import JSONResponse
        return JSONResponse({
            "name": "weather",
            "version": "1.0.0",
            "transport": "sse",
            "endpoints": {
                "sse": "/sse",
                "messages": "/messages/"
            },
            "tools": tool_names
        })
    
    # Define SSE handler
    async def handle_sse(request: Request):
        # Note: Must maintain SSE stream - cannot return JSONResponse on error
        # Errors should be handled by the SSE transport or logged
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
        # Return empty response after SSE connection closes
        return Response()
    
    # Create Starlette app with routes
    app = Starlette(
        routes=[
            Route("/", endpoint=handle_root, methods=["GET"]),
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ]
    )
    
    # Run with uvicorn
    config = uvicorn.Config(
        app, 
        host=host, 
        port=port, 
        log_level="info",
        access_log=False  # Reduce log noise
    )
    server_instance = uvicorn.Server(config)
    await server_instance.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        print(f"\nERROR: Server failed to start: {e}")
        import traceback
        traceback.print_exc()
        raise
