"use strict";
const http = require("http");
const https = require("https");
const { URL } = require("url");

const listenPort = Number(process.env.PLAIBOOK_HTTP1_PROXY_PORT || process.env.PROXY_PORT || 18080);
const restUpstream = new URL("https://api.cursor.com");
const rpcUpstream = new URL("https://api2.cursor.sh");

function pickUpstream(req) {
  const path = req.url || "/";
  if (path.startsWith("/v1/") || path === "/v1") return restUpstream;
  return rpcUpstream;
}

const server = http.createServer((req, res) => {
  const upstream = pickUpstream(req);
  const headers = { ...req.headers };
  delete headers.connection;
  headers.host = upstream.host;

  const opts = {
    protocol: upstream.protocol,
    hostname: upstream.hostname,
    port: upstream.port || 443,
    method: req.method,
    path: req.url,
    headers,
    servername: upstream.hostname,
  };

  const up = https.request(opts, (upRes) => {
    const outHeaders = { ...upRes.headers };
    delete outHeaders.connection;
    res.writeHead(upRes.statusCode || 502, outHeaders);
    upRes.pipe(res);
  });
  up.on("error", (err) => {
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "text/plain" });
    }
    res.end("proxy_error");
  });
  req.pipe(up);
});

server.listen(listenPort, "127.0.0.1");
