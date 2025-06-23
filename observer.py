#!/usr/bin/env python3
"""
Script Observador de Google Sheets para Asignación de Aulas
División de Ciencias Sociales y Humanidades
Universidad Autónoma Metropolitana - Xochimilco

Este script monitorea cambios en la hoja de cálculo de asignación de aulas
y notifica a través de WebSockets cuando detecta modificaciones.

Autor: Daniel Limón <dani@dlimon.net>
Fecha: Junio 2025
"""

import os
import sys
import time
import json
import hashlib
import logging
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Importaciones de Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# Configuración de logging profesional
def setup_logging():
    """
    Configura el sistema de logging para el script.
    Los logs se guardan tanto en archivo como en consola.
    """
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # Crear directorio de logs si no existe
    #log_dir = Path('/var/log/sheets-observer')
    log_dir = Path('logs')
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Configurar logging con rotación diaria
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_dir / 'observer.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    return logging.getLogger(__name__)

# Dataclasses para estructurar los datos
@dataclass
class TimeSlot:
    """Representa una franja horaria específica"""
    start_time: str
    end_time: str
    subject: str
    professor: str
    program: str
    
    def is_empty(self) -> bool:
        """Verifica si la franja horaria está vacía"""
        return not (self.subject or self.professor or self.program)

@dataclass
class ClassroomSchedule:
    """Representa el horario completo de un aula"""
    monday: List[TimeSlot]
    tuesday: List[TimeSlot]
    wednesday: List[TimeSlot]
    thursday: List[TimeSlot]
    friday: List[TimeSlot]
    
    def to_dict(self) -> Dict:
        """Convierte el horario a diccionario para JSON"""
        return {
            'monday': [slot.__dict__ for slot in self.monday],
            'tuesday': [slot.__dict__ for slot in self.tuesday],
            'wednesday': [slot.__dict__ for slot in self.wednesday],
            'thursday': [slot.__dict__ for slot in self.thursday],
            'friday': [slot.__dict__ for slot in self.friday]
        }

@dataclass
class Classroom:
    """Representa un aula completa con toda su información"""
    number: str
    building: str
    name: str
    capacity: int
    schedule: ClassroomSchedule
    last_updated: str
    
    def to_dict(self) -> Dict:
        """Convierte el aula a diccionario para JSON"""
        return {
            'number': self.number,
            'building': self.building,
            'name': self.name,
            'capacity': self.capacity,
            'schedule': self.schedule.to_dict(),
            'last_updated': self.last_updated
        }

class SheetsConfiguration:
    """
    Maneja la configuración del script, incluyendo credenciales
    y parámetros de funcionamiento.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._load_environment_variables()
        self._validate_configuration()
    
    def _load_environment_variables(self):
        """Carga las variables de entorno necesarias"""
        # Rutas y credenciales
        self.credentials_path = os.getenv('GOOGLE_CREDENTIALS_PATH', 
                                        '/opt/sheets-observer/credentials.json')
        self.sheets_document_id = os.getenv('SHEETS_DOCUMENT_ID')
        self.worksheet_name = os.getenv('WORKSHEET_NAME', 'Hoja 1')
        
        # Configuración de la API WebSocket
        self.api_base_url = os.getenv('API_BASE_URL', 'http://localhost:8000')
        self.webhook_endpoint = f"{self.api_base_url}/api/sheets/update"
        
        # Parámetros de funcionamiento
        self.check_interval = int(os.getenv('CHECK_INTERVAL_MINUTES', '3'))
        self.max_retries = int(os.getenv('MAX_RETRIES', '5'))
        self.retry_delay = int(os.getenv('RETRY_DELAY_SECONDS', '30'))
        
        # Configuración de cache
        self.cache_file = os.getenv('CACHE_FILE', '/tmp/sheets_cache.json')
    
    def _validate_configuration(self):
        """Valida que la configuración sea correcta"""
        if not self.sheets_document_id:
            raise ValueError("SHEETS_DOCUMENT_ID es requerido en las variables de entorno")
        
        if not Path(self.credentials_path).exists():
            raise FileNotFoundError(f"Archivo de credenciales no encontrado: {self.credentials_path}")
        
        self.logger.info("Configuración validada correctamente")

class SheetsParser:
    """
    Parsea los datos de Google Sheets y los convierte en objetos estructurados.
    Esta clase entiende el formato específico de la plantilla de aulas.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Definir las franjas horarias según la plantilla
        self.time_slots = [
            ("07:00", "08:00"), ("08:00", "09:00"), ("09:00", "10:00"),
            ("10:00", "11:00"), ("11:00", "12:00"), ("12:00", "13:00"),
            ("13:00", "14:00"), ("14:00", "15:00"), ("15:00", "16:00"),
            ("16:00", "17:00"), ("17:00", "18:00"), ("18:00", "19:00"),
            ("19:00", "20:00"), ("20:00", "21:00")
        ]
        
        # Columnas donde están los días de la semana
        self.day_columns = {
            'monday': 5,    # Columna E
            'tuesday': 6,   # Columna F  
            'wednesday': 7, # Columna G
            'thursday': 8,  # Columna H
            'friday': 9     # Columna I
        }
    
    def parse_worksheet_data(self, worksheet_data: List[List[str]]) -> List[Classroom]:
        """
        Parsea todos los datos de la hoja y devuelve una lista de aulas.
        
        Args:
            worksheet_data: Datos raw de Google Sheets (lista de filas)
            
        Returns:
            Lista de objetos Classroom parseados
        """
        classrooms = []
        current_row = 0
        
        self.logger.info(f"Iniciando serialización de {len(worksheet_data)} filas")
        start_row = 7  # índice 7 = fila 8 (donde comienzan las aulas)
        for current_row in range(start_row, len(worksheet_data)):
            classroom_data = self._try_parse_classroom_at_row(
                data=worksheet_data,
                row_index=current_row)
            if classroom_data:
                classrooms.append(classroom_data)

                #debug
                # self.logger.info(f"Aula serializada: {classroom_data.name}")
                # self.logger.info(f"Datos completos del aula: {json.dumps(classroom_data.to_dict(), indent=2)}")

        
        self.logger.info(f"Serialización completada: {len(classrooms)} aulas encontradas")
        return classrooms
    
    def _try_parse_classroom_at_row(self, data: List[List[str]], row_index: int) -> Optional[Classroom]:
        """
        Intenta parsear un aula comenzando en la fila especificada.
        
        Args:
            data: Datos completos de la hoja
            row_index: Índice de la fila donde podría comenzar un aula
            
        Returns:
            Objeto Classroom si se encontró uno válido, None si no
        """
        try:
            # # Verificar si hay suficientes filas restantes
            if row_index >= len(data) or not data[row_index]:
                return None
            
            # Obtener la fila que podría contener info básica del aula
            header_row = self._get_row_safely(data, row_index)

            # Verificar si esta fila contiene información de aula
            classroom_info = self._extract_classroom_basic_info(header_row)
            if not classroom_info:
                return None
            
            # Parsear el horario de las siguientes 14 filas
            schedule = self._parse_classroom_schedule(data, row_index)
            
            return Classroom(
                number=classroom_info['number'],
                building=classroom_info['building'],
                name=classroom_info['name'],
                capacity=classroom_info['capacity'],
                schedule=schedule,
                last_updated=datetime.now().isoformat()
            )
            
        except Exception as e:
            self.logger.warning(f"Error serializando aula en fila {row_index}: {e}")
            return None
    
    def _extract_classroom_basic_info(self, row: List[str]) -> Optional[Dict]:
        """
        Extrae información básica del aula de una fila.
        
        Formato esperado: [NO, EDIF, AULA, CUPO, ...]
        """
        row_check = row[1:5]  # Solo necesitamos los primeros 4 campos

        # try:
        #     row_check[0] = int(row_check[0])  # Convertir a entero para validar el número
        #     print(type(row_check[0]))
        # except (ValueError, IndexError):
        #     self.logger.debug("Fila no contiene un número de aula válido")
        #     return None
        # return
        
        try:
            # Verificar que los primeros campos tengan contenido relevante
            number = row_check[0].strip()
            building = row_check[1].strip()
            name = row_check[2].strip()
            capacity_str = row_check[3]
            
            # Validar que sea una fila de aula válida
            if not (number and building and name):
                return None
            
            #Convertir capacidad a entero
            try:
                capacity = int(capacity_str)
            except ValueError:
                capacity = 0

            return_payload: dict = {
                'number': number,
                'building': building,
                'name': name,
                'capacity': capacity
            }

            return return_payload
            
        except Exception as e:
            self.logger.debug(f"Error extrayendo info básica: {e}")
            return None
###    
    # def _parse_classroom_schedule(self, data: List[List[str]], start_row: int) -> ClassroomSchedule:
    #     """
    #     Parsea el horario completo de un aula.
        
    #     Args:
    #         data: Datos completos de la hoja
    #         start_row: Fila donde comienza la información del aula
    #     """
    #     # Crear diccionario para almacenar horarios por día
    #     daily_schedules = {day: [] for day in self.day_columns.keys()}
        
    #     # Serializar cada franja horaria (14 franjas después de la fila header)
    #     for slot_index, (start_time, end_time) in enumerate(self.time_slots):
    #         row_index = start_row + slot_index + 1  # +1 para saltar la fila header
    #         row_data = self._get_row_safely(data, row_index)
            
    #         # Parsear cada día de la semana para esta franja horaria
    #         for day_name, col_index in self.day_columns.items():
    #             cell_content = row_data[col_index] if col_index < len(row_data) else ""
    #             time_slot = self._parse_time_slot(cell_content, start_time, end_time)
    #             daily_schedules[day_name].append(time_slot)
        
    #     return ClassroomSchedule(
    #         monday=daily_schedules['monday'],
    #         tuesday=daily_schedules['tuesday'],
    #         wednesday=daily_schedules['wednesday'],
    #         thursday=daily_schedules['thursday'],
    #         friday=daily_schedules['friday']
    #     )
    
    # def _parse_time_slot(self, cell_content: str, start_time: str, end_time: str) -> TimeSlot:
    #     """
    #     Parsea el contenido de una celda para crear un TimeSlot.
        
    #     Args:
    #         cell_content: Contenido de la celda
    #         start_time: Hora de inicio de la franja
    #         end_time: Hora de fin de la franja
    #     """
    #     if not cell_content or cell_content.strip() == "":
    #         return TimeSlot(start_time, end_time, "", "", "")
        
    #     # Limpiar el contenido
    #     content = cell_content.strip()
        
    #     # Intentar extraer programa, materia y profesor
    #     # El formato suele ser: PROGRAMA\nMateria\nProfesor
    #     lines = content.split('\n')
        
    #     program = lines[0] if len(lines) > 0 else ""
    #     subject = lines[1] if len(lines) > 1 else ""
    #     professor = lines[2] if len(lines) > 2 else ""
        
    #     # Si hay más líneas, combinarlas
    #     if len(lines) > 3:
    #         professor = " ".join(lines[2:])
        
    #     return TimeSlot(start_time, end_time, subject, professor, program)
###
    def _parse_classroom_schedule(self, data: List[List[str]], start_row: int) -> ClassroomSchedule:
        """
        Serializa el horario completo de un aula desde una sola fila con estructura horizontal.
        
        Args:
            data: Datos completos de la hoja
            start_row: Fila donde comienza la información del aula
        """
        # Obtener la fila completa del aula
        row_data = self._get_row_safely(data, start_row)
        if not row_data:
            return ClassroomSchedule.empty()
        
        # Configuración de bloques horarios
        total_days = 5  # Lunes a Viernes
        slots_per_day = 14
        start_col = 5  # Columna F (0-indexed)

        # Crear diccionario para almacenar horarios por día
        daily_schedules = {day: [] for day in self.day_columns.keys()}
        days_order = list(self.day_columns.keys())  # ['monday', 'tuesday', ...]

        # Iterar por cada franja horaria (0-13)
        for slot_index in range(slots_per_day):
            start_time, end_time = self.time_slots[slot_index]
            
            # Iterar por cada día de la semana
            for day_index, day_name in enumerate(days_order):
                # Calcular posición horizontal: columna base + (día * 14) + slot
                col_index = start_col + (day_index * slots_per_day) + slot_index
                
                # Obtener contenido seguro de la celda
                cell_content = row_data[col_index] if col_index < len(row_data) else ""
                
                time_slot = self._parse_time_slot(cell_content, start_time, end_time)
                daily_schedules[day_name].append(time_slot)
        
        return ClassroomSchedule(
            monday=daily_schedules['monday'],
            tuesday=daily_schedules['tuesday'],
            wednesday=daily_schedules['wednesday'],
            thursday=daily_schedules['thursday'],
            friday=daily_schedules['friday']
        )

    # Mantenemos igual el parser de celdas
    def _parse_time_slot(self, cell_content: str, start_time: str, end_time: str) -> TimeSlot:
        if not cell_content or cell_content.strip() == "":
            return TimeSlot(start_time, end_time, "", "", "")
        
        content = cell_content.strip()
        lines = content.split('\n')
        
        program = lines[0] if lines else ""
        subject = lines[1] if len(lines) > 1 else ""
        professor = lines[2] if len(lines) > 2 else ""
        
        if len(lines) > 3:
            professor = " ".join(lines[2:])
        
        return TimeSlot(start_time, end_time, subject, professor, program)
    
    def _get_row_safely(self, data: List[List[str]], row_index: int) -> List[str]:
        """
        Obtiene una fila de manera segura, manejando índices fuera de rango.
        """
        if row_index < len(data):
            return data[row_index]
        return []

class SheetsObserver:
    """
    Clase principal que observa cambios en Google Sheets y notifica cuando ocurren.
    """
    
    def __init__(self, config: SheetsConfiguration):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.parser = SheetsParser()
        
        # Estado interno
        self.last_data_hash = None
        self.last_successful_check = None
        self.consecutive_errors = 0
        
        # Inicializar cliente de Google Sheets
        self._initialize_sheets_client()
    
    def _initialize_sheets_client(self):
        """
        Inicializa el cliente de Google Sheets con las credenciales de servicio.
        """
        try:
            # Definir los scopes necesarios
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets.readonly',
                'https://www.googleapis.com/auth/drive.readonly'
            ]
            
            # Cargar credenciales
            credentials = Credentials.from_service_account_file(
                self.config.credentials_path,
                scopes=scopes
            )
            
            # Crear cliente de gspread
            self.gc = gspread.authorize(credentials)
            
            # Abrir la hoja de cálculo<
            self.spreadsheet = self.gc.open_by_key(self.config.sheets_document_id)
            self.worksheet = self.spreadsheet.worksheet(self.config.worksheet_name)
            
            self.logger.info("Cliente de Google Sheets inicializado correctamente")

        except gspread.SpreadsheetNotFound:
            self.logger.error("Documento no encontrado. Verifica el ID y los permisos")
            raise
            
        except Exception as e:
            self.logger.error(f"Error inicializando cliente de Google Sheets: {e}")
            raise
    
    def get_current_data(self) -> List[Classroom]:
        """
        Obtiene los datos actuales de la hoja de cálculo y los parsea.
        
        Returns:
            Lista de aulas parseadas
        """
        try:
            # Obtener todos los datos de la hoja
            raw_data = self.worksheet.get_all_values()
            self.logger.info(f"Datos obtenidos de la hoja: {len(raw_data)} filas")
            
            if not raw_data:
                self.logger.warning("La hoja de cálculo está vacía")
                return []
            
            # Parsear los datos
            classrooms = self.parser.parse_worksheet_data(raw_data)
            
            self.logger.info(f"Datos procesados: {len(classrooms)} aulas")
            return classrooms

            
        except Exception as e:
            self.logger.error(f"Error obteniendo datos actuales: {e}")
            raise
    
    def calculate_data_hash(self, classrooms: List[Classroom]) -> str:
        """
        Calcula un hash MD5 de los datos para detectar cambios.
        
        Args:
            classrooms: Lista de aulas
            
        Returns:
            Hash MD5 como string hexadecimal
        """
        # Convertir todas las aulas a diccionarios
        data_dict = [classroom.to_dict() for classroom in classrooms]
        
        # Convertir a JSON string de manera determinística
        json_string = json.dumps(data_dict, sort_keys=True, ensure_ascii=False)
        
        # Calcular hash MD5
        return hashlib.md5(json_string.encode('utf-8')).hexdigest()
    
    def has_data_changed(self, classrooms: List[Classroom]) -> bool:
        """
        Verifica si los datos han cambiado desde la última verificación.
        
        Args:
            classrooms: Datos actuales
            
        Returns:
            True si los datos cambiaron, False si no

        """
        current_hash = self.calculate_data_hash(classrooms)

        self.logger.debug(f"Hash actual de datos: {current_hash}")
        
        if self.last_data_hash is None:
            # Primera ejecución
            self.last_data_hash = current_hash
            return True
        
        self.logger.debug(f"Hash previo de datos: {self.last_data_hash}")
        
        if current_hash != self.last_data_hash:
            self.logger.info("Cambios detectados en la hoja de cálculo")
            self.last_data_hash = current_hash
            return True
        
        return False
    
    def notify_changes(self, classrooms: List[Classroom]):
        """
        Notifica los cambios a la API WebSocket.
        
        Args:
            classrooms: Datos actualizados para enviar
        """
        try:
            # Preparar payload
            payload = {
                'timestamp': datetime.now().isoformat(),
                'classrooms': [classroom.to_dict() for classroom in classrooms],
                'total_classrooms': len(classrooms)
            }

            
            # Enviar a la API
            response = requests.post(
                self.config.webhook_endpoint,
                json=payload,
                timeout=30,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                self.logger.info("Cambios notificados exitosamente a la API")

            else:
                self.logger.warning(f"Respuesta HTTP de la API: Code: {response.status_code}: Text: {response.text}")
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error notificando cambios: {e}")
            # No re-lanzar la excepción para que el script continúe funcionando
    
    def save_cache(self, classrooms: List[Classroom]):
        """
        Guarda los datos actuales en cache para recuperación en caso de reinicio.
        """
        try:
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'data_hash': self.last_data_hash,
                'classrooms': [classroom.to_dict() for classroom in classrooms]
            }
            
            with open(self.config.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
                
            self.logger.debug("Cache guardado exitosamente")
            
        except Exception as e:
            self.logger.warning(f"Error guardando cache: {e}")
    
    def load_cache(self) -> Optional[str]:
        """
        Carga el hash de datos desde el cache para continuidad en reinicios.
        
        Returns:
            Hash de datos previos si existe, None si no
        """
        try:
            if not Path(self.config.cache_file).exists():
                return None
            
            with open(self.config.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # Verificar que el cache no sea muy antiguo (más de 1 día)
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            if datetime.now() - cache_time > timedelta(days=1):
                self.logger.info("Cache demasiado antiguo, ignorando")
                return None
            
            self.logger.info("Cache cargado exitosamente")
            return cache_data.get('data_hash')
            
        except Exception as e:
            self.logger.warning(f"Error cargando cache: {e}")
            return None
    
    async def run_observation_cycle(self):
        """
        Ejecuta un ciclo completo de observación: obtener datos, verificar cambios, notificar.
        """
        try:
            # Obtener datos actuales
            classrooms = self.get_current_data()
            # Verificar si hubo cambios
            if self.has_data_changed(classrooms):
                self.logger.info("Cambios detectados, notificando...")
                self.notify_changes(classrooms)
                self.save_cache(classrooms)
            else:
                self.logger.debug("No se detectaron cambios")
            
            # Actualizar timestamp de último check exitoso
            self.last_successful_check = datetime.now()
            self.consecutive_errors = 0
            
        except Exception as e:
            self.consecutive_errors += 1
            self.logger.error(f"Error en ciclo de observación (intento {self.consecutive_errors}): {e}")
            
            # Si hay muchos errores consecutivos, esperar más tiempo
            if self.consecutive_errors >= self.config.max_retries:
                self.logger.critical("Demasiados errores consecutivos, el script podría tener problemas serios")
    
    async def start_monitoring(self):
        """
        Inicia el monitoreo continuo de la hoja de cálculo.
        """
        self.logger.info("Iniciando monitoreo de Google Sheets...")
        
        # Cargar cache si existe
        cached_hash = self.load_cache()
        if cached_hash:
            self.last_data_hash = cached_hash
        
        # Ciclo principal de monitoreo
        while True:
            try:
                await self.run_observation_cycle()
                
                # Esperar el intervalo configurado antes del próximo check
                #await asyncio.sleep(self.config.check_interval * 60)
                await asyncio.sleep(20)
                
            except KeyboardInterrupt:
                self.logger.info("Monitoreo interrumpido por el usuario")
                break
            except Exception as e:
                self.logger.error(f"Error inesperado en el ciclo principal: {e}")
                # Esperar un poco antes de reintentar
                await asyncio.sleep(self.config.retry_delay)

def create_env_template():
    """
    Crea un archivo .env de ejemplo con todas las variables necesarias.
    """
    env_template = """# Configuración del Observador de Google Sheets

# Credenciales de Google Sheets (ruta al archivo JSON)
GOOGLE_CREDENTIALS_PATH=/opt/sheets-observer/credentials.json

# ID del documento de Google Sheets (obtenido de la URL)
SHEETS_DOCUMENT_ID=tu_document_id_aqui

# Nombre de la hoja de trabajo dentro del documento
WORKSHEET_NAME=Hoja 1

# URL base de tu API WebSocket
API_BASE_URL=http://localhost:8000

# Intervalo de verificación en minutos
CHECK_INTERVAL_MINUTES=3

# Configuración de reintentos
MAX_RETRIES=5
RETRY_DELAY_SECONDS=30

# Archivo de cache
CACHE_FILE=/tmp/sheets_cache.json
"""
    
    with open('.env.example', 'w') as f:
        f.write(env_template)
    
    print("Archivo .env.example creado. Cópialo a .env y configura tus valores.")

async def main():
    """
    Función principal del script.
    """
    # Configurar logging
    logger = setup_logging()
    
    try:
        # Verificar si se pide crear template de configuración
        if len(sys.argv) > 1 and sys.argv[1] == '--create-env':
            create_env_template()
            return
        
        logger.info("=== Iniciando Script Observador de Google Sheets ===")
        
        # Cargar configuración
        config = SheetsConfiguration()
        
        # Crear observador
        observer = SheetsObserver(config)
        
        # Iniciar monitoreo
        await observer.start_monitoring()
        
    except KeyboardInterrupt:
        logger.info("Script terminado por el usuario")
    except Exception as e:
        logger.critical(f"Error crítico: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Ejecutar el script
    asyncio.run(main())
