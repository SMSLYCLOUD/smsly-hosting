# Cloudflare WebSocket Configuration Guide

This guide provides instructions for optimizing Cloudflare settings for WebSocket connections to improve the stability of the service console.

## Cloudflare WebSocket Timeouts

By default, Cloudflare has a 100-second timeout for WebSocket connections. This can cause disconnects if there's no activity for more than 100 seconds.

## Recommended Cloudflare Settings

### 1. Enterprise Plan Settings (If Available)

If you have a Cloudflare Enterprise plan, you can increase the WebSocket timeout:

1. Go to the Cloudflare dashboard
2. Navigate to Network → WebSockets
3. Increase the timeout to at least 5 minutes (300 seconds)

### 2. Page Rules (Enterprise Plan Required)

Create a Page Rule specifically for your WebSocket endpoint:

1. Go to the Cloudflare dashboard
2. Navigate to Rules → Page Rules
3. Add a new Page Rule for your WebSocket path (e.g., `wss://yourdomain.com/ws/*`)
4. Set "WebSocket Connection Timeout" to 300 seconds or higher

### 3. Cloudflare Spectrum (Enterprise Plan Required)

For maximum WebSocket reliability, consider using Cloudflare Spectrum:

1. Go to the Cloudflare dashboard
2. Navigate to Network → Spectrum
3. Add an application for your WebSocket service
4. Set the protocol to "websocket"
5. Configure the origin to point to your backend server

### 4. Free/Pro/Business Plan Workarounds

If you don't have an Enterprise plan, you'll need to work within the 100-second timeout limitation:

1. Ensure your frontend sends heartbeats every 30 seconds (already implemented)
2. Configure your backend to respond to heartbeats promptly (already implemented)
3. Use the monitoring system to track disconnects and identify patterns

## Testing Cloudflare WebSocket Configuration

To verify your Cloudflare WebSocket configuration:

1. Open the service console in your browser
2. Open the browser's developer tools
3. Go to the Network tab and filter for WebSocket connections
4. Monitor the connection for at least 5 minutes
5. Check if heartbeats are being sent and received properly

## Troubleshooting

If you continue to experience disconnects:

1. Check the Cloudflare logs for any connection termination events
2. Verify that heartbeats are being sent and received properly
3. Consider using a direct connection (bypassing Cloudflare) for testing
4. Use the monitoring system to identify patterns in disconnects

## Additional Resources

- [Cloudflare WebSocket Documentation](https://developers.cloudflare.com/fundamentals/websockets/)
- [Cloudflare Spectrum Documentation](https://developers.cloudflare.com/spectrum/)
- [WebSocket Best Practices](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API/Writing_WebSocket_client_applications)