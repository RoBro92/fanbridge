import os
import pty
import tty
import select
import sys
import json
import time

def main():
    m1, s1 = pty.openpty()
    port_diy = os.ttyname(s1)
    
    m2, s2 = pty.openpty()
    port_official = os.ttyname(s2)
    
    try: os.unlink('/tmp/ttyFAN_DIY')
    except: pass
    os.symlink(port_diy, '/tmp/ttyFAN_DIY')
    
    try: os.unlink('/tmp/ttyFAN_OFFICIAL')
    except: pass
    os.symlink(port_official, '/tmp/ttyFAN_OFFICIAL')
    
    print("Dummy controllers running!")
    print(f"DIY Controller:      /tmp/ttyFAN_DIY -> {port_diy}")
    print(f"Official Controller: /tmp/ttyFAN_OFFICIAL -> {port_official}")
    print("Keep this script running to simulate the serial devices.")
    
    # State
    fan_speed = {"diy": 0, "official": 0}
    
    try:
        while True:
            r, _, _ = select.select([m1, m2], [], [])
            
            for master in r:
                ctrl_type = "diy" if master == m1 else "official"
                data = os.read(master, 1024).decode('utf-8')
                lines = data.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    if line == "ID?":
                        response = f"FANBRIDGE_{ctrl_type.upper()}\n"
                        os.write(master, response.encode('utf-8'))
                    elif line.startswith("PWM "):
                        try:
                            val = int(line.split(" ")[1])
                            fan_speed[ctrl_type] = val
                            os.write(master, f"OK {val}\n".encode('utf-8'))
                        except ValueError:
                            os.write(master, b"ERR\n")
                    elif line == "STATUS":
                        spd = fan_speed[ctrl_type]
                        telemetry = {
                            "bus_v": 12.1 if ctrl_type == "official" else 0.0,
                            "current_a": 1.5 if ctrl_type == "official" else 0.0,
                            "fans": [
                                {"rpm": spd * 30 if spd > 0 else 0, "pwm_percent": spd, "state": "running" if spd > 0 else "offline"}
                                for _ in range(6)
                            ]
                        }
                        os.write(master, (json.dumps(telemetry) + "\n").encode('utf-8'))
                    else:
                        os.write(master, b"OK\n")
                        
    except KeyboardInterrupt:
        print("\nExiting dummy controller.")
    finally:
        try: os.unlink('/tmp/ttyFAN_DIY')
        except: pass
        try: os.unlink('/tmp/ttyFAN_OFFICIAL')
        except: pass
        os.close(m1)
        os.close(s1)
        os.close(m2)
        os.close(s2)

if __name__ == "__main__":
    main()
