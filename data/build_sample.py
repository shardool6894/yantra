"""Builds the fictional sample manual (PDF) and its pre-generated extraction cache.

Run once from the project root:  python data/build_sample.py

The PDF is rendered from the same data that is written to the extraction cache, so the cache is a
hand-authored seed of what ingestion should produce for this manual, not real LLM output.
"""
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).parent
PDF_PATH = HERE / "manuals" / "hp200_manual.pdf"
CACHE_DIR = HERE / "extractions"

MACHINE = "Hydrapress HP-200 Hydraulic Press"


def cause(description, check, answers, steps, tech=None):
    return {"description": description, "check": check, "answers": answers, "steps": steps, "tech": tech}


FAULTS = [
    {
        "title": "Ram does not move",
        "symptoms": [
            "ram does not move when the cycle is started",
            "press will not go down",
            "motor is running but the ram stays still",
            "ram not moving down or up",
            "hydraulic cylinder not responding",
        ],
        "questions": [
            ("Is the hydraulic motor running? (Listen for it and check whether the motor fan is turning.)",
             ["Yes, the motor is running", "No, the motor is silent"]),
            ("What does the pressure gauge show when you start a cycle?",
             ["Zero or almost zero", "Pressure builds up normally"]),
        ],
        "causes": [
            cause("Motor is not starting: emergency stop is engaged or the motor overload relay has tripped.",
                  "The motor is silent and the motor fan is not turning.",
                  {0: [1], 1: [0]},
                  ["Check that the red emergency stop button is released: twist it clockwise until it pops out.",
                   "Press the green MOTOR START button once.",
                   "If the motor still does not start, press the external overload reset knob on the cabinet door. Do not open the cabinet.",
                   "Press MOTOR START again and confirm the motor runs."]),
            cause("Hydraulic oil level is too low.",
                  "The motor is running, the gauge reads zero, and the sight glass shows oil below the MIN mark.",
                  {0: [0], 1: [0]},
                  ["WARNING: Stop the motor with the red STOP button before opening the filler cap.",
                   "Read the oil sight glass on the reservoir. The level must be between the MIN and MAX marks.",
                   "Remove the filler cap and top up with hydraulic oil ISO VG 46 until the level reaches the MAX mark.",
                   "Refit the filler cap and start the motor.",
                   "Run three empty cycles and check the sight glass again. Top up if the level fell below MAX."]),
            cause("Directional valve solenoid Y1 is not energised (blown fuse F4 or loose connector).",
                  "The motor runs and the gauge shows pressure, but the ram does not move and the green LED on valve Y1 stays off.",
                  {0: [0], 1: [1]},
                  ["WARNING: Press the emergency stop before touching any connector or fuse.",
                   "Check that the plug on valve Y1 is fully seated and its locking ring is tight.",
                   "Open fuse holder F4 on the control panel. Replace the fuse with a 2 A glass fuse if the wire inside is broken.",
                   "Release the emergency stop, start the motor and start a cycle.",
                   "Confirm the green LED on valve Y1 lights up and the ram moves."]),
        ],
    },
    {
        "title": "Ram moves slowly",
        "symptoms": [
            "ram is moving very slowly",
            "press cycle takes much longer than usual",
            "slow pressing stroke",
            "ram speed has dropped",
        ],
        "questions": [
            ("What does the oil thermometer on the reservoir show?", ["55 °C or below", "Above 55 °C"]),
            ("What colour is the return filter clogging indicator?", ["Green", "Red"]),
        ],
        "causes": [
            cause("Return line filter is clogged.",
                  "The filter indicator is in the red zone.",
                  {1: [1]},
                  ["WARNING: Stop the motor and wait 2 minutes for the pressure to drop to zero before opening the filter housing.",
                   "Unscrew the filter bowl and remove the old element.",
                   "Fit a new element, part number HF-200-R, and tighten the bowl by hand plus a quarter turn.",
                   "Start the motor and check that the indicator returns to green.",
                   "Wipe away any spilled oil around the filter."]),
            cause("Oil is overheating because the cooler fan is not running or the cooler is blocked.",
                  "The thermometer reads above 55 °C and the filter indicator is green.",
                  {0: [1], 1: [0]},
                  ["WARNING: Hot oil can cause burns. Let the press cool for 15 minutes before touching the cooler.",
                   "Check that the cooler fan spins when the oil is above 50 °C. If it does not, check fan fuse F5.",
                   "Brush dust and lint off the cooler fins with a soft brush.",
                   "Restart the press and watch the thermometer for 10 minutes. It must stay at or below 55 °C."]),
            cause("Flow control valve FC1 has been turned down.",
                  "The oil temperature is normal, the filter indicator is green, and the ram has been slow since the first cycle of the shift.",
                  {0: [0], 1: [0]},
                  ["Locate flow control valve FC1 beside the main cylinder.",
                   "Loosen the lock nut on the FC1 adjuster.",
                   "Turn the adjuster anticlockwise one turn at a time, running one cycle after each turn, until the stroke takes about 8 seconds.",
                   "Tighten the lock nut and mark the adjuster with a paint pen."]),
        ],
    },
    {
        "title": "Pressing force too low (pressure does not reach the set value)",
        "symptoms": [
            "pressure gauge does not reach the set pressure",
            "press is weak and cannot form the part",
            "low pressing force",
            "system pressure is low",
        ],
        "questions": [
            ("Does the ram slowly creep downward when it is held under load?",
             ["Yes, it creeps down", "No, it holds its position"]),
            ("Is the oil in the reservoir foamy or milky?",
             ["Yes, foamy or milky", "No, the oil looks normal"]),
            ("Turn the relief valve RV1 adjuster one turn clockwise. Does the gauge pressure rise?",
             ["Yes, the pressure rises", "No, nothing changes"]),
        ],
        "causes": [
            cause("Relief valve RV1 is set too low.",
                  "The pressure rises when RV1 is turned clockwise, the ram holds its position, and the oil looks normal.",
                  {0: [1], 1: [1], 2: [0]},
                  ["Loosen the lock nut on relief valve RV1.",
                   "Turn the adjuster clockwise in quarter turns while watching the gauge.",
                   "WARNING: Never set the pressure above 160 bar.",
                   "Tighten the lock nut when the gauge reads 160 bar during a pressing stroke."]),
            cause("Air in the hydraulic system.",
                  "The oil in the reservoir is foamy or milky and the pump is noisier than normal.",
                  {1: [0]},
                  ["WARNING: Stop the motor before touching any fitting.",
                   "Check that the oil level is above the MIN mark and top up if needed.",
                   "Check that the pump suction hose clamps are tight (hand tight plus a quarter turn).",
                   "Start the motor at low pressure and open the bleed screw at the top of the cylinder half a turn. Close it when oil comes out without bubbles.",
                   "Run five empty cycles. The foam should clear within 10 minutes."]),
            cause("Piston seal inside the main cylinder is leaking.",
                  "The ram creeps downward when held under load, and turning RV1 has no effect.",
                  {0: [0], 1: [1], 2: [1]},
                  ["Stop the press and press the emergency stop.",
                   "Switch off the main isolator and apply your lock-out padlock."],
                  tech="The cylinder must be dismantled to replace the piston seal."),
            cause("Relief valve RV1 is stuck partly open because of dirt.",
                  "The ram holds its position and the oil is clean, but turning RV1 clockwise changes nothing.",
                  {0: [1], 1: [1], 2: [1]},
                  ["WARNING: Stop the motor and wait for the pressure to drop to zero before removing RV1.",
                   "Note the number of adjuster turns, then unscrew the RV1 cartridge with a 24 mm spanner.",
                   "Rinse the cartridge in clean hydraulic oil. Do not use rags or compressed air.",
                   "Refit the cartridge and turn the adjuster back to the noted position.",
                   "Start the motor, set the pressure to 160 bar on the gauge and tighten the lock nut."]),
        ],
    },
    {
        "title": "Hydraulic oil leak",
        "symptoms": [
            "oil is leaking from the press",
            "puddle of oil under the machine",
            "oil dripping from the cylinder or hoses",
            "wet oil patches on the press",
        ],
        "questions": [
            ("Where is the oil coming from?",
             ["Around the cylinder rod", "From a hose or fitting", "From the reservoir (tank)"]),
        ],
        "causes": [
            cause("Rod seal on the main cylinder is worn.",
                  "Oil runs down the cylinder rod and collects at the cylinder gland.",
                  {0: [0]},
                  ["Stop the press and press the emergency stop.",
                   "Place a drip tray under the cylinder and keep people away from the area."],
                  tech="Replacing the rod seal means removing the cylinder from the frame."),
            cause("A hose or fitting is loose or damaged.",
                  "A hose or its end fitting is wet with oil, or the hose is cracked, swollen or chafed.",
                  {0: [1]},
                  ["WARNING: Stop the motor and wait 2 minutes for the pressure to drop to zero.",
                   "Wipe the area dry and find the exact point where the oil comes out.",
                   "If a fitting is wet, tighten it with a 22 mm spanner to 35 Nm.",
                   "If the hose itself is cracked, swollen or chafed, stop work and fit a new hose, part number HP-200-H3.",
                   "Run three cycles and check that the fitting stays dry."]),
            cause("Reservoir filler cap or tank cover gasket is loose.",
                  "Oil seeps from the top of the reservoir around the filler cap or the tank cover.",
                  {0: [2]},
                  ["WARNING: Stop the motor before touching the reservoir.",
                   "Tighten the filler cap by hand.",
                   "Tighten the 8 tank cover screws in a cross pattern to 10 Nm.",
                   "Wipe up the oil and check again after three cycles."]),
        ],
    },
    {
        "title": "Cycle will not start (start buttons have no effect)",
        "symptoms": [
            "cycle start button does nothing",
            "press does not start when I press both buttons",
            "machine will not start a cycle",
            "start buttons are not responding",
        ],
        "questions": [
            ("Is the Control Power lamp on the panel lit?", ["Yes, it is lit", "No, it is off"]),
            ("Is the amber Safety Circuit lamp lit?", ["Yes, it is lit", "No, it is off"]),
            ("Is the front guard door fully closed?", ["Yes, fully closed", "No, it is open or not latched"]),
        ],
        "causes": [
            cause("Control fuse F2 is blown.",
                  "The Control Power lamp is off.",
                  {0: [1]},
                  ["WARNING: Press the emergency stop and switch off the main isolator before opening the fuse holder.",
                   "Open fuse holder F2 on the side of the panel and look at the 4 A fuse. Replace it if the wire inside is broken.",
                   "Close the fuse holder, switch the isolator on and release the emergency stop.",
                   "If the new fuse blows straight away, stop and call a technician."]),
            cause("The guard door is open or not latched.",
                  "Control Power is lit, the Safety Circuit lamp is lit, and the guard door is open.",
                  {0: [0], 1: [0], 2: [1]},
                  ["Close the front guard door fully until it clicks.",
                   "Check that nothing, such as a tool or a part, is trapped in the door edge.",
                   "Confirm the Safety Circuit lamp goes off.",
                   "Press both start buttons together to start the cycle."]),
            cause("Guard door switch S1 is misaligned or faulty.",
                  "Control Power is lit, the Safety Circuit lamp is lit, and the guard door is fully closed.",
                  {0: [0], 1: [0], 2: [0]},
                  ["Stop work and leave the guard door closed.",
                   "Do not bypass or tape over the door switch."],
                  tech="Safety interlocks must only be adjusted or replaced by a qualified technician and must never be bypassed."),
            cause("The two start buttons are not pressed together.",
                  "Control Power is lit, the Safety Circuit lamp is off, and nothing happens when the buttons are pressed one after the other.",
                  {0: [0], 1: [1], 2: [0]},
                  ["Press and hold both green start buttons at the same time. They must be pressed within 0.5 seconds of each other.",
                   "Check that neither button is stuck in. A stuck button must spring back when released.",
                   "Wipe oil and dirt off both buttons with a dry cloth, then try again."]),
        ],
    },
    {
        "title": "Hydraulic pump is noisy",
        "symptoms": [
            "hydraulic pump is making a loud noise",
            "whining sound from the pump",
            "knocking or grinding noise from the pump unit",
            "pump is very noisy",
        ],
        "questions": [
            ("What is the oil level in the sight glass?", ["Below the MIN mark", "Between MIN and MAX"]),
            ("What does the pump inlet vacuum gauge show?", ["Needle in the red zone", "Needle in the green zone"]),
        ],
        "causes": [
            cause("Oil level is too low, so the pump sucks in air.",
                  "The sight glass shows oil below the MIN mark.",
                  {0: [0]},
                  ["WARNING: Stop the motor with the red STOP button before opening the filler cap.",
                   "Remove the filler cap and top up with hydraulic oil ISO VG 46 until the level reaches the MAX mark.",
                   "Refit the filler cap, start the motor and listen. The noise should stop within a minute."]),
            cause("The pump suction strainer is blocked.",
                  "The oil level is normal and the pump inlet vacuum gauge is in the red zone.",
                  {0: [1], 1: [0]},
                  ["WARNING: Stop the motor and close suction ball valve V1 before removing the strainer cover.",
                   "Remove the strainer cover and pull out the mesh basket.",
                   "Rinse the basket in clean hydraulic oil and refit it.",
                   "Open V1 fully, restart the motor and confirm the vacuum gauge reads in the green zone."]),
            cause("Pump bearings are worn.",
                  "The oil level is normal, the vacuum gauge is in the green zone, and the pump makes a steady grinding or knocking noise.",
                  {0: [1], 1: [1]},
                  ["Stop the press and press the emergency stop.",
                   "Switch off the main isolator and apply your lock-out padlock."],
                  tech="The pump must be removed from the press and rebuilt."),
        ],
    },
]


def extraction():
    """The structure ingestion stores: same shape as the LLM output."""
    faults = []
    for f in FAULTS:
        faults.append({
            "title": f["title"],
            "symptoms": f["symptoms"],
            "questions": [{"text": t, "options": o} for t, o in f["questions"]],
            "causes": [
                {
                    "description": c["description"],
                    "needs_technician": c["tech"] is not None,
                    "reason": c["tech"] or "",
                    "answers": [{"q": q, "opts": o} for q, o in c["answers"].items()],
                    "steps": [{"text": s, "is_warning": s.startswith("WARNING")} for s in c["steps"]],
                }
                for c in f["causes"]
            ],
        })
    return {"machine": MACHINE, "faults": faults}


# ---------- PDF ----------
styles = getSampleStyleSheet()
body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14)
small = ParagraphStyle("small", parent=body, fontSize=9, leading=12)
h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=10)
h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13, spaceBefore=12, spaceAfter=6)
h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=10.5, spaceBefore=8, spaceAfter=2)
warn = ParagraphStyle("warn", parent=body, textColor=colors.HexColor("#8a1c1c"))


def P(text, style=body):
    return Paragraph(escape(text), style)


def build_pdf():
    s = []
    s += [Spacer(1, 60 * mm), P("Hydrapress HP-200", ParagraphStyle("t", parent=h1, fontSize=30, leading=36)),
          P("Hydraulic Press: Operation and Maintenance Manual", h2),
          Spacer(1, 10 * mm),
          P("Document HP200-OM-04. Sample manual for the YANTRA prototype. The machine and all part numbers are fictional.", small),
          PageBreak()]

    s += [P("1. Safety", h1),
          P("Read this section before operating the press. Only trained operators may use the machine."),
          P("1.1 Always keep both hands on the two start buttons during the pressing stroke. Never reach into the die area while the motor is running."),
          P("1.2 The emergency stop button (red, mushroom head) stops the motor and removes control power. Release it by twisting clockwise."),
          P("1.3 The front guard door is fitted with a safety interlock. Never bypass, tape over or adjust the interlock switch."),
          P("1.4 Hydraulic oil under pressure can pierce skin. Before opening any fitting, stop the motor and wait 2 minutes for the system to depressurise."),
          P("1.5 Hot oil and the oil cooler can cause burns. Allow the machine to cool before servicing."),
          P("1.6 Repairs marked 'qualified technician' in this manual must not be attempted by operators."),
          P("2. Technical data", h1)]
    rows = [["Item", "Value"], ["Maximum pressing force", "200 kN"], ["Maximum system pressure", "160 bar"],
            ["Motor", "7.5 kW, 400 V, 3 phase"], ["Hydraulic oil", "ISO VG 46, 40 litres"],
            ["Normal oil temperature", "40 to 55 °C"], ["Stroke time (empty)", "about 8 seconds"]]
    t = Table([[P(a, small), P(b, small)] for a, b in rows], colWidths=[70 * mm, 90 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    s += [t, PageBreak()]

    s += [P("3. Routine maintenance", h1),
          P("Daily: check the oil level in the sight glass and look for leaks around the cylinder and hoses."),
          P("Weekly: check the return filter indicator and clean the oil cooler fins."),
          P("Every 500 hours: replace the return filter element. Every 2000 hours: change the hydraulic oil."),
          P("Every year: have the guard door interlock tested by a qualified technician."),
          PageBreak()]

    s += [P("4. Troubleshooting", h1),
          P("Find the fault in the list below. Each fault shows the symptoms, then the possible causes with a check to identify the cause and the remedy steps. Carry out the steps in order.")]
    for n, f in enumerate(FAULTS, 1):
        s.append(P(f"Fault 4.{n}: {f['title']}", h2))
        s.append(P("Symptoms: " + "; ".join(f["symptoms"]) + ".", body))
        for m, c in enumerate(f["causes"], 1):
            s.append(P(f"Possible cause {n}.{m}: {c['description']}", h3))
            s.append(P("How to check: " + c["check"], body))
            if c["tech"]:
                s.append(P("Qualified technician required. " + c["tech"] + " Do not attempt this repair yourself.", warn))
                s.append(P("Make the machine safe:", small))
            else:
                s.append(P("Remedy:", small))
            for i, step in enumerate(c["steps"], 1):
                s.append(P(f"{i}. {step}", warn if step.startswith("WARNING") else small))
    SimpleDocTemplate(str(PDF_PATH), pagesize=A4, invariant=1, title="Hydrapress HP-200 Manual",
                      leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm).build(s)


if __name__ == "__main__":
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    build_pdf()
    sha = hashlib.sha256(PDF_PATH.read_bytes()).hexdigest()
    out = CACHE_DIR / f"{sha[:16]}.json"
    out.write_text(json.dumps(extraction(), indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", PDF_PATH, "\nwrote", out)
