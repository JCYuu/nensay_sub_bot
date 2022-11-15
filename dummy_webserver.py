#!/usr/bin/python3.9
# Extremely simple webserver made with the stdlib just for testing purposes

import time
import sys

from http.server import BaseHTTPRequestHandler, HTTPServer

try:
	port=int(sys.argv[1])
except:
	port=80

class MyServer(BaseHTTPRequestHandler):
	def do_GET(self):
		self.send_response(200)
		self.send_header("Content-type","text/plain")
		self.end_headers()
		self.wfile.write(bytes("It's working...","utf-8"))

webServer = HTTPServer(("localhost",port),MyServer)
webServer.serve_forever()
