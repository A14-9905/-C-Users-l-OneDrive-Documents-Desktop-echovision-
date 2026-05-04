from gpiozero import DistanceSensor, PWMOutputDevice, Buzzer, DigitalInputDevice
from time import sleep

# ---------------- GPIO PINS ----------------
TRIG_PIN = 23
ECHO_PIN = 24
BUZZER_PIN = 18
MOTOR_PIN = 27
IR_PIN = 17

# ---------------- DEVICES ----------------
sensor = DistanceSensor(
    echo=ECHO_PIN,
    trigger=TRIG_PIN,
    max_distance=2.0,
    queue_len=5
)

buzzer = Buzzer(BUZZER_PIN)
motor = PWMOutputDevice(MOTOR_PIN)

ir_sensor = DigitalInputDevice(IR_PIN, pull_up=False)

print("SMART STICK SYSTEM STARTED ??")

# ---------------- IR FUNCTION ----------------
def ir_hole_alert():
    """
    IR logic:
    - Both lights ON ? safe ? no alert
    - One light OFF ? hole ? alert
    """

    if not ir_sensor.value:
        # SAFE
        print("IR SAFE (ground detected)")
        return False
    else:
        # DANGER
        print("?? HOLE DETECTED!")

        motor.value = 1.0

        buzzer.on()
        sleep(0.2)
        buzzer.off()
        sleep(0.1)

        return True


# ---------------- ULTRASONIC FUNCTION ----------------
def ultrasonic_alert(distance_cm):

    if distance_cm <= 0 or distance_cm > 400:
        print("Invalid reading")
        motor.value = 0
        buzzer.off()
        sleep(0.1)
        return

    if distance_cm <= 20:
        print("?? VERY CLOSE")
        motor.value = 1.0
        buzzer.on()
        sleep(0.07)
        buzzer.off()
        sleep(0.05)

    elif distance_cm <= 40:
        print("?? CLOSE")
        motor.value = 0.8
        buzzer.on()
        sleep(0.1)
        buzzer.off()
        sleep(0.08)

    elif distance_cm <= 70:
        print("?? AHEAD")
        motor.value = 0.6
        buzzer.on()
        sleep(0.15)
        buzzer.off()
        sleep(0.12)

    elif distance_cm <= 100:
        print("?? FAR")
        motor.value = 0.4
        buzzer.on()
        sleep(0.2)
        buzzer.off()
        sleep(0.2)

    else:
        print("? CLEAR")
        motor.value = 0
        buzzer.off()
        sleep(0.1)


# ---------------- MAIN LOOP ----------------
try:
    while True:

        # Step 1: Check IR (highest priority)
        hole = ir_hole_alert()

        # Step 2: If no hole ? use ultrasonic
        if not hole:
            distance_cm = sensor.distance * 100
            print(f"Distance: {distance_cm:.1f} cm")
            ultrasonic_alert(distance_cm)

except KeyboardInterrupt:
    print("Stopping...")
    motor.off()
    buzzer.off()
    sensor.close()
    ir_sensor.close()
