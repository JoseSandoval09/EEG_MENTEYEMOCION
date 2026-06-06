"""

COLUMNAS DEL CSV:
  Timestamp                → momento en que se cerró la ventana de 1 segundo
  MuestrasEnVentana        → cuántas muestras raw se acumularon (~512 ideal)
  MuestrasRuidosas         → cuántas tenían poor_signal > umbral
  Attention                → eSense del chip (0-100)
  Meditation               → eSense del chip (0-100)
  PoorSignal               → calidad de señal (0=perfecta, 200=sin contacto)

  --- Bandas del ASIC (chip NeuroSky, algoritmo propietario) ---
  Delta_ASIC, Theta_ASIC, Alpha_ASIC, Beta_ASIC, Gamma_ASIC
  CLI_ASIC, Eng_ASIC

  --- Bandas FFT sin filtrar (igual que v2, para comparación) ---
  Delta_FFT, Theta_FFT, Alpha_FFT, Beta_FFT, Gamma_FFT
  CLI_FFT, Eng_FFT

  --- Bandas FFT con filtro pasa-banda 1-40 Hz (NUEVAS en v3) ---
  Delta_FFT_filt, Theta_FFT_filt, Alpha_FFT_filt, Beta_FFT_filt, Gamma_FFT_filt
  CLI_FFT_filt, Eng_FFT_filt

REQUISITOS:
  pip install numpy scipy
  (mindwave.py debe estar en la misma carpeta)
"""

import csv
import threading
import time
import datetime
import queue
import numpy as np
from scipy.signal import butter, filtfilt
import mindwave
import sys


# ─────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────
PUERTO           = 'COM3'   # Puerto del dongle
FREQ_MUESTREO    = 512      # Hz — frecuencia de muestreo del MindWave
POOR_SIGNAL_MAX  = 25       # Muestras con poor_signal > este valor = ruidosas

# ── Parámetros del filtro pasa-banda (NUEVO en v3) ────────────────────────
FILTRO_LOW_HZ    = 1.0      # frecuencia de corte inferior en Hz
FILTRO_HIGH_HZ   = 40.0     # frecuencia de corte superior en Hz
FILTRO_ORDEN     = 4        # orden del filtro Butterworth
                            # orden 4 = buen balance entre pendiente de corte
                            # y estabilidad numérica para señales EEG
# ─────────────────────────────────────────────


# ── Rangos de frecuencia para cada banda (Hz) ─────────────────────────────
BANDAS = {
    'Delta': (0.5,  4.0),
    'Theta': (4.0,  8.0),
    'Alpha': (8.0, 13.0),
    'Beta' : (13.0, 30.0),
    'Gamma': (30.0, 40.0),
}


# ─────────────────────────────────────────────
#  FILTRO PASA-BANDA (NUEVO en v3)
# ─────────────────────────────────────────────

def construir_filtro_butterworth(low_hz, high_hz, fs, orden):
    """
    Calcula los coeficientes b, a del filtro Butterworth pasa-banda.

    Se llama UNA SOLA VEZ al inicio del programa — los coeficientes
    son constantes y se reutilizan en cada ventana de 1 segundo.

    Parámetros:
      low_hz  → frecuencia de corte inferior (1 Hz)
      high_hz → frecuencia de corte superior (40 Hz)
      fs      → frecuencia de muestreo (512 Hz)
      orden   → orden del filtro (4)

    Devuelve:
      b, a → coeficientes del filtro listos para usar con filtfilt()
    """
    # Normalización de Nyquist: las frecuencias se expresan como fracción
    # de la frecuencia de Nyquist (fs/2 = 256 Hz)
    nyquist = fs / 2.0
    low_norm  = low_hz  / nyquist   # 1  / 256 = 0.00390625
    high_norm = high_hz / nyquist   # 40 / 256 = 0.15625

    b, a = butter(orden, [low_norm, high_norm], btype='band')
    return b, a


def aplicar_filtro(muestras, b, a):
    """
    Aplica el filtro pasa-banda sobre la señal raw usando filtfilt.

    filtfilt aplica el filtro DOS VECES:
      1. hacia adelante en el tiempo
      2. hacia atrás en el tiempo
    Esto produce FASE CERO — no desplaza la señal en el tiempo.
    Crítico para EEG porque preserva la sincronía temporal de los eventos.

    Requisito mínimo de muestras para filtfilt:
      padlen = 3 * max(len(a), len(b)) — scipy lo maneja internamente,
      pero necesitamos al menos ~27 muestras para orden 4.
      Con ventanas de 512 muestras estamos muy por encima del mínimo.

    Devuelve:
      numpy array con la señal filtrada, misma longitud que la entrada.
      Si hay error (pocas muestras), devuelve la señal original sin filtrar.
    """
    señal = np.array(muestras, dtype=np.float64)
    min_muestras = 3 * max(len(a), len(b)) + 1

    if len(señal) < min_muestras:
        # Pocas muestras — devolver sin filtrar para no crashear
        return señal

    try:
        return filtfilt(b, a, señal)
    except Exception:
        # Si scipy falla por cualquier razón, devolver sin filtrar
        return señal


# ─────────────────────────────────────────────
#  FFT Y CÁLCULO DE BANDAS 
# ─────────────────────────────────────────────

def calcular_bandas_fft(muestras, fs=FREQ_MUESTREO):
    """
    Recibe lista de valores raw y devuelve potencia por banda.
  
    """
    n = len(muestras)
    if n < 64:
        return {banda: 0 for banda in BANDAS}

    señal = np.array(muestras, dtype=np.float64)
    ventana = np.hanning(n)
    señal_ventana = señal * ventana

    fft_resultado = np.fft.rfft(señal_ventana)
    potencia = (np.abs(fft_resultado) ** 2) / n
    frecuencias = np.fft.rfftfreq(n, d=1.0 / fs)

    resultado = {}
    for nombre, (f_min, f_max) in BANDAS.items():
        indices = np.where((frecuencias >= f_min) & (frecuencias < f_max))[0]
        resultado[nombre] = float(np.sum(potencia[indices]))

    return resultado


def calcular_indices(theta, alpha, beta):
    """
    CLI        = Theta / Alpha
    Engagement = Beta / (Alpha + Theta)
    """
    cli = round(theta / alpha, 4) if alpha > 0 else 0
    eng = round(beta / (alpha + theta), 4) if (alpha + theta) > 0 else 0
    return cli, eng


# ─────────────────────────────────────────────
#  CLASE PRINCIPAL
# ─────────────────────────────────────────────

class GrabadoraPruebas:

    def __init__(self, nombre_sesion):
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.archivo_csv = f'{nombre_sesion}_{ts}.csv'

        # Buffer donde raw_handler deposita cada muestra a 512 Hz
        self.buffer_raw = queue.Queue()

        # Snapshot de bandas ASIC y eSense
        self.lock_waves  = threading.Lock()
        self.waves_snap  = {}
        self.attention   = 0
        self.meditation  = 0
        self.poor_signal = 255

        self.corriendo = False
        self.headset   = None

        # ── Sistema de marcadores ─────────────────────────────────────────
        # El experimentador presiona 1, 2 o 3 para marcar inicio de estímulo
        # La columna Marcador queda vacía en ventanas normales
        # y escribe 'E1', 'E2' o 'E3' en la ventana donde se presionó la tecla
        self.lock_marcador = threading.Lock()
        self.marcador_actual = ''  # vacío = sin marcador en esta ventana
        # ─────────────────────────────────────────────────────────────────

        
        self.b_filt, self.a_filt = construir_filtro_butterworth(
            FILTRO_LOW_HZ,
            FILTRO_HIGH_HZ,
            FREQ_MUESTREO,
            FILTRO_ORDEN
        )
        print(f'Filtro Butterworth pasa-banda {FILTRO_LOW_HZ}–{FILTRO_HIGH_HZ} Hz '
              f'(orden {FILTRO_ORDEN}) inicializado.')
        # ──────────────────────────────────────────────────────────────────

        # Abrir CSV
        self.f   = open(self.archivo_csv, 'w', newline='', encoding='utf-8')
        self.csv = csv.writer(self.f)

        # Encabezados — 18 columnas (bandas de interés + marcador de estímulo)
        self.csv.writerow([
            'Timestamp',
            'MuestrasEnVentana', 'MuestrasRuidosas',
            'Attention', 'Meditation', 'PoorSignal',
            # Bandas ASIC
            'Theta_ASIC', 'Alpha_ASIC', 'Beta_ASIC',
            'CLI_ASIC', 'Eng_ASIC',
            # Bandas FFT filtrada
            'Theta_FFT_filt', 'Alpha_FFT_filt', 'Beta_FFT_filt',
            'CLI_FFT_filt', 'Eng_FFT_filt',
            # Marcador de estímulo — E1, E2, E3 o vacío
            'Marcador',
        ])

    # ── CALLBACKS ─────────────────────────────────────────────────────────

    def raw_handler(self, headset, raw_value):
        """Llamado 512 veces/segundo. Solo encola — no procesa."""
        self.buffer_raw.put((raw_value, headset.poor_signal))

    def waves_handler(self, headset, waves):
        """Llamado ~1 vez/segundo. Guarda snapshot ASIC."""
        with self.lock_waves:
            self.waves_snap  = dict(waves)
            self.attention   = headset.attention
            self.meditation  = headset.meditation
            self.poor_signal = headset.poor_signal

    # ── HILO DE TECLADO ───────────────────────────────────────────────────

    def hilo_teclado(self):
        """
        Escucha teclas en segundo plano sin bloquear la grabación.
        Presiona 1, 2 o 3 para marcar inicio de estímulo.
        Funciona en Windows leyendo msvcrt directamente.
        """
        import msvcrt
        print('\n[TECLAS] Presiona 1=E1  2=E2  3=E3  para marcar estímulos\n')
        while self.corriendo:
            if msvcrt.kbhit():
                tecla = msvcrt.getch().decode('utf-8', errors='ignore')
                if tecla in ('1', '2', '3'):
                    with self.lock_marcador:
                        self.marcador_actual = f'E{tecla}'
                    print(f'\n  ★ MARCADOR E{tecla} registrado\n')
            time.sleep(0.05)

    # ── HILO ESCRITOR ─────────────────────────────────────────────────────

    def hilo_escritor(self):
        """
        Se despierta cada segundo (= una ventana FFT).
          1. Extrae muestras raw del buffer
          2. Descarta ventanas inválidas
          3. Aplica filtro y calcula bandas FFT filtradas
          4. Lee snapshot ASIC
          5. Lee marcador actual y lo resetea
          6. Escribe fila al CSV
          7. Imprime resumen en consola
        """
        ventana = 0

        while self.corriendo:
            time.sleep(1.0)
            ventana += 1
            ts = datetime.datetime.now().isoformat()

            # ── 1. Extraer muestras del buffer ────────────────────────────
            muestras_raw      = []
            muestras_ruidosas = 0

            while not self.buffer_raw.empty():
                try:
                    val, ps = self.buffer_raw.get_nowait()
                    muestras_raw.append(val)
                    if ps > POOR_SIGNAL_MAX:
                        muestras_ruidosas += 1
                except queue.Empty:
                    break

            n_muestras = len(muestras_raw)

            # ── Descartar ventanas inválidas ──────────────────────────────
            if n_muestras > 600:
                print(f'[{ventana:>4}s] ⚠ Descartada — calibración ({n_muestras} muestras)')
                continue
            if n_muestras < 64:
                print(f'[{ventana:>4}s] ⚠ Descartada — sin muestras ({n_muestras})')
                continue

            # ── 2. FFT filtrada ───────────────────────────────────────────
            muestras_filtradas = aplicar_filtro(muestras_raw, self.b_filt, self.a_filt)
            bandas_fft_filt    = calcular_bandas_fft(muestras_filtradas)
            theta_fft_filt     = bandas_fft_filt['Theta']
            alpha_fft_filt     = bandas_fft_filt['Alpha']
            beta_fft_filt      = bandas_fft_filt['Beta']
            cli_fft_filt, eng_fft_filt = calcular_indices(
                theta_fft_filt, alpha_fft_filt, beta_fft_filt
            )

            # ── 3. Leer snapshot ASIC ─────────────────────────────────────
            with self.lock_waves:
                w   = dict(self.waves_snap)
                att = self.attention
                med = self.meditation
                ps  = self.poor_signal

            theta_asic = w.get('theta',     0)
            alpha_asic = w.get('low-alpha', 0) + w.get('high-alpha', 0)
            beta_asic  = w.get('low-beta',  0) + w.get('high-beta',  0)
            cli_asic, eng_asic = calcular_indices(theta_asic, alpha_asic, beta_asic)

            # ── 4. Leer y resetear marcador ───────────────────────────────
            with self.lock_marcador:
                marcador = self.marcador_actual
                self.marcador_actual = ''  # resetear después de escribirlo

            # ── 5. Escribir fila CSV ───────────────────────────────────────
            self.csv.writerow([
                ts,
                n_muestras, muestras_ruidosas,
                att, med, ps,
                # ASIC
                theta_asic, alpha_asic, beta_asic,
                cli_asic, eng_asic,
                # FFT filtrada
                round(theta_fft_filt,  2),
                round(alpha_fft_filt,  2),
                round(beta_fft_filt,   2),
                cli_fft_filt, eng_fft_filt,
                # Marcador
                marcador,
            ])
            self.f.flush()

            # ── 6. Consola ────────────────────────────────────────────────
            calidad  = '✓ OK' if ps == 0 else f'⚠ RUIDO={ps}'
            mark_str = f' ★{marcador}' if marcador else ''
            print(
                f'[{ventana:>4}s] '
                f'Muestras:{n_muestras:>3} | '
                f'Atn:{att:>3} Med:{med:>3} | '
                f'CLI_ASIC:{cli_asic:>6.3f}  '
                f'CLI_FFT_filt:{cli_fft_filt:>6.3f} | '
                f'{calidad}{mark_str}'
            )

    # ── CONTROL PRINCIPAL ─────────────────────────────────────────────────

    def iniciar(self):
        print(f'\nArchivo de salida: {self.archivo_csv}')
        print(f'\nConectando al MindWave en {PUERTO}...')

        self.headset = mindwave.Headset(PUERTO)
        self.headset.raw_value_handlers.append(self.raw_handler)
        self.headset.waves_handlers.append(self.waves_handler)

        print('Conectado. Calibrando 10 segundos, quédate quieto...')
        time.sleep(10)

        print('\n¡GRABANDO! — Ctrl+C para detener\n')
        print(f'{"Vent":>6} {"Muestras":>9} {"Atn":>4} {"Med":>4} '
              f'{"CLI_ASIC":>10} {"CLI_FFT":>9} {"CLI_FFT_filt":>14} {"Señal":>10}')
        print('-' * 75)

        self.corriendo = True
        t = threading.Thread(target=self.hilo_escritor, daemon=True)
        t.start()

        # Hilo de teclado para marcadores
        tk = threading.Thread(target=self.hilo_teclado, daemon=True)
        tk.start()

        try:
            while self.corriendo:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.corriendo = False

        time.sleep(2.0)
        self.f.flush()
        self.f.close()
        self.headset.stop()

        print(f'\nGrabación finalizada → {self.archivo_csv}')


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

if __name__ == '__main__':
    print('=== Grabadora EEG v5 — MindWave Mobile 2 ===')
    print('    Filtro pasa-banda Butterworth 1–40 Hz activo')
    print('    Descarte de ventanas de calibración activo')
    print('    Sistema de marcadores: teclas 1, 2, 3 → E1, E2, E3\n')
    nombre = input('Nombre de la sesión (ej. prueba_crucigrama): ').strip()
    if not nombre:
        nombre = 'sesion'

    grabadora = GrabadoraPruebas(nombre)
    grabadora.iniciar()