import time
import random
import os

DISKS_INI = "dev-unraid/disks.ini"

def generate_data():
    os.makedirs(os.path.dirname(DISKS_INI), exist_ok=True)
    while True:
        with open(DISKS_INI, "w") as f:
            f.write(f"""[disk1]
name="disk1"
device="sda"
temp="{random.randint(30, 45)}"
spundown="0"

[disk2]
name="disk2"
device="sdb"
temp="{random.randint(35, 50)}"
spundown="0"

[parity]
name="parity"
device="sdc"
temp="{random.randint(32, 48)}"
spundown="0"

[cache]
name="cache"
device="nvme0n1"
temp="{random.randint(40, 60)}"
spundown="0"
""")
        time.sleep(3)

if __name__ == "__main__":
    generate_data()
