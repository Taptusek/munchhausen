import time
import board
import busio
import csv
import adafruit_ds3502
from adafruit_ina219 import INA219
import matplotlib.pyplot as plt

def main():
    print("Inicjalizacja magistrali I2C...")
    i2c = busio.I2C(board.SCL, board.SDA)

    # Inicjalizacja potencjometru cyfrowego DS3502 (domyślny adres to 0x28)
    try:
        ds3502 = adafruit_ds3502.DS3502(i2c)
        print("[OK] Potencjometr cyfrowy DS3502 zainicjowany.")
    except Exception as e:
        print(f"[!] Błąd inicjalizacji DS3502: {e}")
        return

    # Inicjalizacja miernika mocy INA219 (wykorzystujemy adres 0x45 z głównego pliku sensor.py)
    try:
        ina = INA219(i2c, addr=0x45)
        print("[OK] Watomierz INA219 zainicjowany.")
    except Exception as e:
        print(f"[!] Błąd inicjalizacji INA219: {e}")
        return

    print("\n[INFO] Skrypt został uruchomiony w trybie ciągłym.")
    print("[INFO] Charakterystyka I-V będzie wywoływana automatycznie co 120 sekund.")
    
    while True:
        filename = f"charakterystyka_IV_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        
        voltages = []
        currents = []
        powers = []
        
        print(f"\n--- Nowy Cykl Pomiarowy: {time.strftime('%H:%M:%S')} ---")
        print(f"Zapisywanie logów punktów do pliku: {filename}")
        print("-" * 55)
        print(f"{'Wiper (Krok)':>13} | {'Napięcie (V)':>12} | {'Prąd (mA)':>10} | {'Moc (mW)':>10}")
        print("-" * 55)
    
        # Ustawiamy potencjometr na startową pozycję układu (pusty / zwarty)
        ds3502.wiper = 0
        time.sleep(1.0) # Początkowe uspokojenie napięć dla MOSFETa i pomiarowego INA
    
        # Rozpoczęcie iteracji i zapisu do pliku o ustrukturyzowanej formie tablicowej CSV
        with open(filename, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Wiper_Pos", "Napiecie_V", "Prad_mA", "Moc_mW"])
    
            for wiper_val in range(128):
                # Ustawienie wycieraczki potencjometru (i tym samym napięcia V_gs na MOSFECIE)
                ds3502.wiper = wiper_val
                
                # Czas na naładowanie/rozładowanie pojemności prądu bramki tranzystora MOSFET
                time.sleep(0.15)
                
                # Czas zaszalał – odczyt punktu pracy panelu fotowoltaicznego badany przez INA219!
                v = ina.bus_voltage
                i = ina.current
                p = v * i # Ręczne policzenie mocy (P = U * I), by wyhaczyć precyzyjnie mikrowaty
                
                # Zerowanie ujemnych odczytów prądu by uniknąć spadków krzywej przez nagłe szumy tła (zera obwodowego)
                i = max(0.0, i)
                p = max(0.0, p)
                
                voltages.append(v)
                currents.append(i)
                powers.append(p)
                
                # Zrzucenie próbki do CSV
                writer.writerow([wiper_val, round(v, 3), round(i, 2), round(p, 2)])
                
                # Wyrysuj tabele co 5 kroków, by nie robić w Terminalu śmieci
                if wiper_val % 5 == 0 or wiper_val == 127:
                    print(f"{wiper_val:>13} | {v:>12.3f} | {i:>10.2f} | {p:>10.2f}")
    
        print("-" * 55)
        print("Skanowanie panelu zakończone. Wyłączanie obciążenia panelu (Wiper = 0).")
        ds3502.wiper = 0 # Zwolnienie obciążeń w tranzystorze uchroni drenaż
    
        # ---> Etap Graficzny : Skrypt kończy pętlę i rysuje fizyczne charakterystyki szukając punktów MPPT <---
        print("Generowanie zdjęć wykresów MPPT...")
        fig, ax1 = plt.subplots(figsize=(10, 6))
    
        color = 'tab:red'
        ax1.set_xlabel('Napięcie Panelu Słonecznego [V]')
        ax1.set_ylabel('Prąd Pobierany przez układ [mA]', color=color)
        ax1.plot(voltages, currents, color=color, marker='o', linestyle='-', markersize=4, label="Prąd złącza I-V")
        ax1.tick_params(axis='y', labelcolor=color)
        ax1.grid(True)
    
        # Stworzenie drugiej skali osi oś Y (Z prawej) dla piku mocy P-V
        ax2 = ax1.twinx()  
        color = 'tab:blue'
        ax2.set_ylabel('Moc Emitowana [mW]', color=color)  
        ax2.plot(voltages, powers, color=color, marker='s', linestyle='--', markersize=4, label="Moc złącza P-V")
        ax2.tick_params(axis='y', labelcolor=color)
    
        fig.tight_layout()  
        plt.title(f"Charakterystyka Panelu Słonecznego ({time.strftime('%H:%M:%S')})")
        
        # Zapis wykresu krzywej do pliku fizycznego obok wygenerowanego CSV
        plot_filename = filename.replace('.csv', '.png')
        plt.savefig(plot_filename)
        print(f"[OK] Wykres wygenerowany: {plot_filename}")
        
        # Usuwamy widok z pamięci by nie zawalił RAMu malinki!
        plt.close(fig)
        
        print("\n[ZzZ] Czekam 2 minuty (120 sekund) na powtórzenie testu...")
        time.sleep(120.0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Wciśnięcie kontrol c awaryjnie wyciągnie bramkę tranzystora
        print("\nPrzerwano przez użytkownika. Wyłączam tranzystor awaryjnie!")
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            ds3502 = adafruit_ds3502.DS3502(i2c)
            ds3502.wiper = 0
        except:
            pass
