import json
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = 'localhost'
PORT = 777
SAVE_PATH = 'dcsh_posg_aulas_data.json'

class RequestHandler(BaseHTTPRequestHandler):
    """Manejador de peticiones POST para recibir datos"""
    
    def do_POST(self):
        if self.path == '/api/sheets/update':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            print(f"🔗 Recibido POST en {self.path} con {content_length} bytes de datos.")
            try:
                # Parsear y guardar datos
                data = json.loads(post_data)
                self._save_data(data)
                
                # Respuesta exitosa
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = json.dumps({"status": "success", "message": "Datos guardados"}).encode()
                self.wfile.write(response)
                
            except Exception as e:
                # Manejo de errores
                self.send_response(500)
                self.end_headers()
                error_msg = json.dumps({"status": "error", "message": str(e)}).encode()
                self.wfile.write(error_msg)
        else:
            self.send_response(404)
            self.end_headers()
    
    def _save_data(self, data):
        """Guarda los datos en un archivo JSON"""
        with open(SAVE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✓ Datos guardados en {SAVE_PATH}")

def run_server():
    """Inicia el servidor HTTP"""
    server = HTTPServer((HOST, PORT), RequestHandler)
    print(f"🌐 Servidor escuchando en http://{HOST}:{PORT}")
    print(f"🔔 Endpoint: POST /api/sheets/update")
    print(f"💾 Los datos se guardarán en: {SAVE_PATH}")
    server.serve_forever()

if __name__ == '__main__':
    run_server()