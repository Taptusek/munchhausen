import os
import csv
import time
import json
import serial
import board
import adafruit_gps
import busio
import sys
import meshtastic
import meshtastic.serial_interface
import adafruit_dht

USE_GUI = "-nogui" not in sys.argv
if USE_GUI:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[!] Biblioteka matplotlib nie jest zainstalowana. Dodaj -nogui do komendy, by uruchomić skrypt bez podglądu.")
        sys.exit(1)
from adafruit_bme280 import basic as adafruit_bme280
from smbus2 import SMBus

# --- POPRAWIONA KLASA MS5611 (GY-63) ---
class MS5611:
    def __init__(self, address=0x76):
        self.bus = SMBus(1)
        self.addr = address
        
        try:
            # 1. Reset czujnika - kluczowy dla poprawnego startu
            self.bus.write_byte(self.addr, 0x1E)
            time.sleep(0.1)
            
            # 2. Odczyt współczynników kalibracji PROM (rejestry 0xA2 do 0xAC)
            # C1 = Ciśnienie, C2 = Ciśnienie, C3 = Temp, C4 = Temp, C5 = Temp, C6 = Temp
            self.c = []
            for i in range(1, 7):
                data = self.bus.read_i2c_block_data(self.addr, 0xA0 + (i * 2), 2)
                self.c.append((data[0] << 8) | data[1])
            
            if sum(self.c) == 0:
                print("[!] OSTRZEŻENIE: MS5611 zwrócił pustą kalibrację. Sprawdź zasilanie/piny PS i CSB.")
        except Exception as e:
            raise Exception(f"Błąd komunikacji z MS5611: {e}")

    def read_data(self):
        try:
            # Odczyt temperatury (D2) - OSR 4096
            self.bus.write_byte(self.addr, 0x58)
            time.sleep(0.012) # Czas na konwersję
            d2_raw = self.bus.read_i2c_block_data(self.addr, 0x00, 3)
            d2 = (d2_raw[0] << 16) | (d2_raw[1] << 8) | d2_raw[2]

            # Odczyt ciśnienia (D1) - OSR 4096
            self.bus.write_byte(self.addr, 0x48)
            time.sleep(0.012)
            d1_raw = self.bus.read_i2c_block_data(self.addr, 0x00, 3)
            d1 = (d1_raw[0] << 16) | (d1_raw[1] << 8) | d1_raw[2]

            if d1 == 0 or d2 == 0:
                return None, None

            # Oficjalne obliczenia kompensacji (MS5611 datasheet)
            dT = d2 - (self.c[4] * 256)
            temp = 2000 + (dT * self.c[5] / 8388608)
            
            off = self.c[1] * 65536 + (self.c[3] * dT) / 128
            sens = self.c[0] * 32768 + (self.c[2] * dT) / 256
            pres = (d1 * sens / 2097152 - off) / 32768
            
            return temp / 100.0, pres / 100.0
        except:
            return None, None

# --- INICJALIZACJA SYSTEMU ---
print("Inicjalizacja magistrali I2C...")
i2c = busio.I2C(board.SCL, board.SDA)

# 1. BME280 (Adres 0x77)
try:
    bme = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x77)
    print("[OK] BME280 (0x77) zainicjowany.")
except Exception as e:
    bme = None
    print(f"[!] BME280 pominięty: {e}")

# 2. MS5611 (Adres 0x76)
try:
    ms_sensor = MS5611(address=0x76)
    print("[OK] MS5611 (0x76) zainicjowany.")
except Exception as e:
    ms_sensor = None
    print(f"[!] MS5611 pominięty: {e}")

# 3. Watomierz wydzielony do skryptu panel-control.py

# 4. DHT11 (GPIO 4)
dht_sensor = adafruit_dht.DHT11(board.D4)

# 5. LoRa Meshtastic (XIAO nRF52840 przez USB)
try:
    # Automatyczne połączenie ze znalezionym urządzeniem na portach USB.
    meshtastic_node = meshtastic.serial_interface.SerialInterface()
    print("[OK] Połączono z modułem Meshtastic przez port USB API.")
except Exception as e:
    meshtastic_node = None
    print(f"[!] Błąd połączenia z modułem Meshtastic: {e}. Sprawdź kabel USB.")

# 6. GPS Air530 (UART)
try:
    # Zmień '/dev/ttyUSB0' na właściwy port podłączenia GPS (np. '/dev/serial1' lub '/dev/ttyS0').
    uart_gps = serial.Serial('/dev/ttyUSB0', baudrate=9600, timeout=1)
    gps = adafruit_gps.GPS(uart_gps, debug=False)
    # Uruchomienie domyślne. Czysty odczyt NMEA.
    print("[OK] GPS Air530 zainicjowany na /dev/ttyUSB0 (9600 bps).")
except Exception as e:
    gps = None
    print(f"[!] Błąd inicjalizacji GPS Air530: {e}.")

def main():
    print("\nRozpoczynam zbieranie danych. Ctrl+C przerywa program.\n")
    
    # 1. Konfiguracja zapisu do formatu CSV
    csv_folder = "csv_data"
    os.makedirs(csv_folder, exist_ok=True)
    filename_time = time.strftime('%Y%m%d_%H%M%S')
    csv_filepath = os.path.join(csv_folder, f"telemetry_{filename_time}.csv")
    print(f"[OK] Dedykowany plik CSV: {csv_filepath}")
    
    csv_file = open(csv_filepath, mode='a', newline='', encoding='utf-8')
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(["Czas", "BME_T", "MS_T", "DHT_T", "BME_P", "MS_P", "BME_H", "DHT_H", "GPS_LAT", "GPS_LON", "GPS_ALT"])
    csv_file.flush()

    # 2. Konfiguracja rysowania wykresów na żywo
    if USE_GUI:
        print("[OK] Inicjalizacja wykresów na żywo...")
        plt.ion() # Tryb interaktywny dla płynnego rysowania
        fig, (ax_t, ax_p, ax_h) = plt.subplots(3, 1, figsize=(10, 8))
        fig.canvas.manager.set_window_title('StratoQuest - Live Telemetry')
        fig.tight_layout(pad=3.0)
        
        # Listy do przechowywania ostatnich M pomiarów
        hist_x = []
        hist_bme_t, hist_ms_t, hist_dht_t = [], [], []
        hist_bme_p, hist_ms_p = [], []
        hist_bme_h, hist_dht_h = [], []
    else:
        print("[i] Tryb -nogui włączony. Rysowanie wykresów pominięte.")

    while True:
        print(f"--- Pomiary z godziny: {time.strftime('%H:%M:%S')} ---")
        
        telemetry = {}

        # Sekcja BME280
        if bme:
            t_bme, p_bme, h_bme = bme.temperature, bme.pressure, bme.humidity
            print(f"BME280  | Temp: {t_bme:.1f} °C | Cis: {p_bme:.1f} hPa | Wilg: {h_bme:.1f} %")
            telemetry['bme_t'] = round(t_bme, 1)
            telemetry['bme_p'] = round(p_bme, 1)
            telemetry['bme_h'] = round(h_bme, 1)
        
        # Sekcja MS5611
        if ms_sensor:
            t_ms, p_ms = ms_sensor.read_data()
            if t_ms is not None:
                print(f"MS5611  | Temp: {t_ms:.2f} °C | Cis: {p_ms:.2f} hPa")
                telemetry['ms_t'] = round(t_ms, 2)
                telemetry['ms_p'] = round(p_ms, 2)
            else:
                print("MS5611  | Błąd odczytu danych surowych.")

        # Sekcja Watomierza zignorowana (przeniesiona do eksperymentów słonecznych)

        # Sekcja DHT11
        try:
            t_dht = dht_sensor.temperature
            h_dht = dht_sensor.humidity
            if t_dht is not None:
                print(f"DHT11   | Temp: {t_dht} °C | Wilg: {h_dht} %")
                telemetry['dht_t'] = t_dht
                telemetry['dht_h'] = h_dht
        except RuntimeError:
            print("DHT11   | Czekam na stabilny odczyt...")

        # Sekcja GPS Air530
        if gps:
            if gps.has_fix:
                lat = gps.latitude
                lon = gps.longitude
                alt = gps.altitude_m
                
                if gps.timestamp_utc:
                    t = gps.timestamp_utc
                    gps_time = f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"
                else:
                    gps_time = "Brak"
                    
                print(f"GPS     | Fix: TAK | Czas: {gps_time} UTC | Lat: {lat:.6f} | Lon: {lon:.6f} | Alt: {alt} m")
                
                if lat is not None and lon is not None:
                    telemetry['gps_lat'] = round(lat, 6)
                    telemetry['gps_lon'] = round(lon, 6)
                if alt is not None:
                    telemetry['gps_alt'] = round(alt, 1)
            else:
                print("GPS     | Szukanie satelitów (Brak Fixa)...")

        # Wysłanie danych przez API Meshtastic (połączenie USB)
        if meshtastic_node and telemetry:
            try:
                message = json.dumps(telemetry)
                # targetId='^all' pośle wiadomość do kanału tekstowego w sieci
                meshtastic_node.sendText(message)
                print(f"LoRa API| Wysłano JSON: {message.strip()}")
            except Exception as e:
                print(f"LoRa API| Błąd wysyłania: {e}")

        print("-" * 45)
        
        # --- Zapis do CSV i Rysowanie Wykresów ---
        obecny_czas = time.strftime('%H:%M:%S')
        
        # Przepisywanie danych ze słownika (None, jeśli czujnik nie odpowiada)
        t_b, t_m, t_d = telemetry.get('bme_t'), telemetry.get('ms_t'), telemetry.get('dht_t')
        p_b, p_m = telemetry.get('bme_p'), telemetry.get('ms_p')
        h_b, h_d = telemetry.get('bme_h'), telemetry.get('dht_h')

        # Zapisz linijkę do pliku CSV
        csv_writer.writerow([
            obecny_czas, t_b, t_m, t_d, p_b, p_m, h_b, h_d,
            telemetry.get('gps_lat'), telemetry.get('gps_lon'), telemetry.get('gps_alt')
        ])
        csv_file.flush() # Natychmiastowe zrzucenie pliku na kartę uodparnia dane na zanik zasilania
        
        if USE_GUI:
            # Zapisz w pamięci do rysowania linii
            hist_x.append(obecny_czas)
            hist_bme_t.append(t_b);    hist_ms_t.append(t_m);    hist_dht_t.append(t_d)
            hist_bme_p.append(p_b);    hist_ms_p.append(p_m)
            hist_bme_h.append(h_b);    hist_dht_h.append(h_d)
    
            if len(hist_x) > 60: # Widok tylko ostatnich 60 pomiarów żeby nie spowolnić programu
                hist_x.pop(0)
                hist_bme_t.pop(0);     hist_ms_t.pop(0);     hist_dht_t.pop(0)
                hist_bme_p.pop(0);     hist_ms_p.pop(0)
                hist_bme_h.pop(0);     hist_dht_h.pop(0)
    
            # 1. Odświeżenie Temperatury
            ax_t.clear(); ax_t.set_title("Temperatura [°C]")
            ax_t.plot(hist_x, hist_bme_t, label="BME280", color="red", marker=".")
            ax_t.plot(hist_x, hist_ms_t, label="MS5611", color="orange", marker=".")
            ax_t.plot(hist_x, hist_dht_t, label="DHT11", color="brown", marker=".")
            ax_t.legend(loc='upper left'); ax_t.grid(True)
            ax_t.set_xticks([]) # Ukrycie osi X
            
            # 2. Odświeżenie Ciśnienia
            ax_p.clear(); ax_p.set_title("Ciśnienie [hPa]")
            ax_p.plot(hist_x, hist_bme_p, label="BME280", color="blue", marker=".")
            ax_p.plot(hist_x, hist_ms_p, label="MS5611", color="cyan", marker=".")
            ax_p.legend(loc='upper left'); ax_p.grid(True)
            ax_p.set_xticks([])
            
            # 3. Odświeżenie Wilgotności
            ax_h.clear(); ax_h.set_title("Wilgotność [%]")
            ax_h.plot(hist_x, hist_bme_h, label="BME280", color="green", marker=".")
            ax_h.plot(hist_x, hist_dht_h, label="DHT11", color="olive", marker=".")
            ax_h.legend(loc='upper left'); ax_h.grid(True)
            
            # Oś X (czas) tylko na najniższym wykresie
            if len(hist_x) > 0:
                ax_h.set_xticks(range(0, len(hist_x), max(1, len(hist_x)//6)))
            ax_h.tick_params(axis='x', rotation=30)
    
            plt.pause(0.01) # Szybkie wyrysowanie buforów bez pełnego zamrażania procedury
        
        # Aktywne oczekiwanie - nasłuchuje w tle bufor GPS z UART co 10ms przez max 2 sekundy:
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < 2.0:
            if gps:
                gps.update()
            time.sleep(0.01)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram zatrzymany przez użytkownika.")
    finally:
        dht_sensor.exit()
        if 'meshtastic_node' in globals() and meshtastic_node:
            meshtastic_node.close()