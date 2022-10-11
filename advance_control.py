#!/usr/bin/env python

# Python 2/3 compatibility imports
from __future__ import print_function

# standard library imports
import json
from logging import lastResort
import subprocess
import time
import datetime
from threading import Thread, Lock

# request HTTP
import requests

# local module imports
from blinker import signal
import gv  # Get access to SIP's settings, gv = global variables
from sip import template_render
from urls import urls  # Get access to SIP's URLs
import web
from webpages import ProtectedPage

# Add a new url to open the data entry page.
# fmt: off
urls.extend(
    [
        u"/advc", u"plugins.advance_control.settings",
        u"/advj", u"plugins.advance_control.settings_json",
        u"/advu", u"plugins.advance_control.update",
        u"/advdisp", u"plugins.advance_control.valve_status_display",
        u"/advsts", u"plugins.advance_control.check_valve_status",
    ]
)
# fmt: on

# Add this plugin to the plugins menu
gv.plugin_menu.append([u"Advance Control", u"/advc"])
gv.plugin_menu.append([u"Advace Control Valve status", u"/advdisp"])

commandsAdv = {}
priorAdv = [0] * len(gv.srvals)

devicesAccessProtection = {}

threadCheckOnLine = None
lastTimeValvesOnLine = {}

runValveOnLine = False

def run_check_valves_on_line_keep_state():
    global lastTimeValvesOnLine

    while runValveOnLine:
        lastTime = datetime.datetime.now()

        for i in range(len(gv.srvals)):
            if commandsAdv[u"typeOutput"][i] == "shellyHTTP" or commandsAdv[u"typeOutput"][i] == "sonOff":
                if commandsAdv[u"typeOutput"][i] == "shellyHTTP":
                    # use credentials, if present
                    if len(commandsAdv[u"deviceUserName"][i]) > 0:
                        userData = commandsAdv[u"deviceUserName"][i] + ":" + commandsAdv[u"devicePassword"][i] + "@"
                    else:
                        userData = ""

                    # TODO: add port

                    statusURL = commandsAdv[u"deviceProtocol"][i] + u"://" + userData + commandsAdv[u"deviceIP"][i] + u"/status"
                else:
                    statusURL = ""

                shellyChannel = "0"
                if commandsAdv[u"deviceModel"][i] == "shell2_2":
                    shellyChannel = "1"

                # TODO: add port

                devicesAccessProtection[i].acquire()
                resposeIsOk, response = httpResquestJSON(statusURL)

                if resposeIsOk == 0:
                    lastTimeValvesOnLine[i] = datetime.datetime.now()

                # if to keep state if not in the correct state change state
                if resposeIsOk == 0 and commandsAdv[u"deviceProtocol"][i] == 1:
                    try:
                        newState = response['relays'][int(shellyChannel)]['ison'] == 'True'
                        if newState and gv.srvals[i] == 0:
                            turnOffURL = commandsAdv[u"deviceProtocol"][i] + u"://" + userData + commandsAdv[u"deviceIP"][i] + u"/relay/" + shellyChannel + u"?turn=off"
                            resposeIsOkOff, response = httpResquestJSON(turnOffURL)
                            if not resposeIsOkOff:
                                print("Fail to turn off in keep state")
                        elif not newState and gv.srvals[i] == 1:
                            turnOnURL = commandsAdv[u"deviceProtocol"][i] + u"://" + userData + commandsAdv[u"deviceIP"][i] + u"/relay/" + shellyChannel + u"?turn=on"
                            resposeIsOkOn, response = httpResquestJSON(turnOnURL)
                            if not resposeIsOkOn:
                                print("Fail to turn on in keep state")
                    except NameError:
                        print("Error, no data found")

                devicesAccessProtection[i].release()      

        nowTime = datetime.datetime.now()

        # read each valve after 30s
        diffTime = nowTime - lastTime
        lastTime = nowTime
        secondsInt = int(diffTime.seconds)
        if 30 - secondsInt > 0:
            sleepTime = 30 - secondsInt
            for k in range(sleepTime):
                if not runValveOnLine:
                    break
                time.sleep(1)

# Read in the commands for this plugin from it's JSON file
def load_commands():
    global commandsAdv, devicesAccessProtection, lastTimeValvesOnLine
    global runValveOnLine, threadCheckOnLine

    try:
        with open(u"./data/advance_control.json", u"r") as f:
            commandsAdv = json.load(f)  # Read the commands from file
    except IOError:  #  If file does not exist create file with defaults.
        commandsAdv = {u"typeOutput": [u""] * gv.sd[u"nst"], u"deviceModel": [u""] * gv.sd[u"nst"], u"deviceIP": [u""] * gv.sd[u"nst"], u"deviceProtocol": [u""] * gv.sd[u"nst"], u"devicePort": [u""] * gv.sd[u"nst"], u"deviceUserName": [u""] * gv.sd[u"nst"], u"devicePassword": [u""] * gv.sd[u"nst"], u"deviceKeepState": [0] * gv.sd[u"nst"], u"on": [u""] * gv.sd[u"nst"], u"off": [u""] * gv.sd[u"nst"], u"useLatch": [0] * gv.sd[u"nst"], u"gpio": 0}

        # set the protocol by default http and port 80
        for i in range(gv.sd[u"nst"]):
            commandsAdv["deviceProtocol"][i] = "http"
            commandsAdv["devicePort"][i] = "80"
        
        #commandsAdv[u"on"][0] = u"echo 'example start command for station 1'"
        #commandsAdv[u"off"][0] = u"echo 'example stop command for station 1'"
        with open(u"./data/advance_control.json", u"w") as f:
            json.dump(commandsAdv, f, indent=4)

    devicesAccessProtection = [Lock()] * gv.sd[u"nst"]
    lastTimeValvesOnLine = [datetime.datetime.now()] * gv.sd[u"nst"]

    runValveOnLine = True
    threadCheckOnLine = Thread(target = run_check_valves_on_line_keep_state)
    threadCheckOnLine.start()

    return


load_commands()

if commandsAdv["gpio"]:
    gv.use_gpio_pins = False
else:
    gv.use_gpio_pins = True         
        

#### output command when signal received ####
def on_zone_change(name, **kw):
    """ Send command when core program signals a change in station state."""
    global priorAdv
    if gv.srvals != priorAdv:  # check for a change
        for i in range(len(gv.srvals)):
            if gv.srvals[i] != priorAdv[i]:  #  this station has changed
                if commandsAdv[u"typeOutput"][i] == "comandLine":
                    # use command line to control valves
                    if gv.srvals[i]:  # station is on
                        command = commandsAdv[u"on"][i]
                        if command:  #  If there is a command for this station:
                            subprocess.call(command.split(), shell=True)
                    else:
                        command = commandsAdv[u"off"][i]
                        if command:
                            subprocess.call(command.split(), shell=True)
                elif commandsAdv[u"typeOutput"][i] == "shellyHTTP" or commandsAdv[u"typeOutput"][i] == "sonOff":
                    #start to lock device to avoid same http requets
                    devicesAccessProtection[i].acquire()

                    # Check type of shelly, if any use name and password, need to check if relay
                    if commandsAdv[u"typeOutput"][i] == "shellyHTTP":
                        # use shelly HTTP protocol
                        # use credentials, if present
                        if len(commandsAdv[u"deviceUserName"][i]) > 0:
                            userData = commandsAdv[u"deviceUserName"][i] + ":" + commandsAdv[u"devicePassword"][i] + "@"
                        else:
                            userData = ""

                        shellyChannel = "0"
                        if commandsAdv[u"deviceModel"][i] == "shell2_2":
                            shellyChannel = "1"

                        # TODO: add port

                        turnOnURL = commandsAdv[u"deviceProtocol"][i] + u"://" + userData + commandsAdv[u"deviceIP"][i] + u"/relay/" + shellyChannel + u"?turn=on"
                        turnOffURL = commandsAdv[u"deviceProtocol"][i] + u"://" + userData + commandsAdv[u"deviceIP"][i] + u"/relay/" + shellyChannel + u"?turn=off"

                        statusURL = commandsAdv[u"deviceProtocol"][i] + u"://" + userData + commandsAdv[u"deviceIP"][i] + u"/status"
                    else:
                        # TODO: SonOff Code
                        turnOnURL = ""
                        turnOffURL = ""

                        statusURL = ""

                    resposeIsOk, response = httpResquestJSON(statusURL)

                    if resposeIsOk == 0 and commandsAdv[u"useLatch"][i] == 0:
                        lastTimeValvesOnLine[i] = datetime.datetime.now()

                        try:
                            lastState = response['relays'][0]['ison'] == 'True'
                        except NameError:
                            print("No data fount in respond")
                            resposeIsOk = 4

                        if gv.srvals[i] and not lastState:  # station is off and new state must be on
                            print("Station ned to be on but it is turn of")
                            resposeIsOkOn, response = httpResquestJSON(turnOnURL)
                            if resposeIsOkOn:
                                resposeIsOk, response = httpResquestJSON(statusURL)

                                if resposeIsOk:
                                    try:
                                        if commandsAdv[u"typeOutput"][i] == "shellyHTTP":
                                            newState = response['relays'][int(shellyChannel)]['ison'] == 'True'
                                        else:
                                            newState = False
                                    except NameError:
                                        print("No data fount in respond from turn on")
                                        resposeIsOk = 5

                                    if newState:
                                        print("Valve is now turn on")
                                    else:
                                        resposeIsOk = 6
                                        print("Fail to turn on")
                            else:
                                print("Unable to turn off")
                        elif not gv.srvals[i] and lastState: #station is turn on but must turn off
                            print("Station ned to be off but it is turn on")
                            resposeIsOkOn, response = httpResquestJSON(turnOffURL)
                            if resposeIsOkOn:
                                resposeIsOk, response = httpResquestJSON(statusURL)

                                if resposeIsOk:
                                    try:
                                        if commandsAdv[u"typeOutput"][i] == "shellyHTTP":
                                            newState = response['relays'][0]['ison'] == 'True'
                                        else:
                                            newState = False
                                    except NameError:
                                        print("No data fount in respond from turn on")
                                        resposeIsOk = 5

                                    if not newState:
                                        print("Valve is now turn off")
                                    else:
                                        resposeIsOk = 6
                                        print("Fail to turn off")
                            else:
                                print("Unable to turn off")
                        else:
                            print("Station is the correct state")
                    elif commandsAdv[u"useLatch"][i] == 1 and resposeIsOk == 0:
                        # use lactch and valve is online
                        print("use latch")
                        resposeIsOkOn, response = httpResquestJSON(turnOnURL)
                        time.sleep(5)
                        if resposeIsOkOn:
                            resposeIsOkOff, response = httpResquestJSON(turnOffURL)
                            if resposeIsOkOff:
                                print("Latch sucess")

                    devicesAccessProtection[i].release()

        priorAdv = gv.srvals[:]
    return

def httpResquestJSON(commandURL):
    # try to get corrent state of network relay
    response = None

    try:
        response = requests.get(commandURL)
        resposeIsOk = 0
    except requests.exceptions.Timeout:
        # Maybe set up for a retry, or continue in a retry loop
        resposeIsOk = 1
        print("Connection time out")
    except requests.exceptions.TooManyRedirects:
        # Tell the user their URL was bad and try a different one
        resposeIsOk = 2
        print("Too many redirections")
    except requests.exceptions.RequestException as e:
        # catastrophic error. bail.
        #raise SystemExit(e)
        resposeIsOk = 3
        print("Fatal error")

    return resposeIsOk, response


zones = signal(u"zone_change")
zones.connect(on_zone_change)

################################################################################
# Web pages:                                                                   #
################################################################################

def check_commands_advance_size():
    global commandsAdv, devicesAccessProtection

    if (
        len(commandsAdv[u"on"]) != gv.sd[u"nst"]
    ):  #  if number of stations has changed, adjust length of on and off lists
        if gv.sd[u"nst"] > len(commandsAdv[u"on"]):
            increase = [""] * (gv.sd[u"nst"] - len(commandsAdv[u"on"]))

            commandsAdv[u"typeOutput"].extend(increase)

            commandsAdv[u"deviceModel"].extend(increase)

            commandsAdv[u"deviceIP"].extend(increase)
            commandsAdv[u"deviceProtocol"].extend(increase)
            commandsAdv[u"devicePort"].extend(increase)

            commandsAdv[u"deviceUserName"].extend(increase)
            commandsAdv[u"devicePassword"].extend(increase)
            commandsAdv[u"deviceKeepState"].extend(increase)

            commandsAdv[u"useLatch"].extend(increase)

            commandsAdv[u"on"].extend(increase)
            commandsAdv[u"off"].extend(increase)

            increaseProtection = [Lock()] * (gv.sd[u"nst"] - len(commandsAdv[u"on"]))
            devicesAccessProtection.extend()
        elif gv.sd[u"nst"] < len(commandsAdv[u"on"]):
            commandsAdv[u"typeOutput"] = commandsAdv[u"typeOutput"][: gv.sd[u"nst"]]

            commandsAdv[u"deviceModel"] = commandsAdv[u"deviceModel"][: gv.sd[u"nst"]]

            commandsAdv[u"deviceIP"] = commandsAdv[u"deviceIP"][: gv.sd[u"nst"]]
            commandsAdv[u"deviceProtocol"] = commandsAdv[u"deviceProtocol"][: gv.sd[u"nst"]]
            commandsAdv[u"devicePort"] = commandsAdv[u"devicePort"][: gv.sd[u"nst"]]

            commandsAdv[u"deviceUserName"] = commandsAdv[u"deviceUserName"][: gv.sd[u"nst"]]
            commandsAdv[u"devicePassword"] = commandsAdv[u"devicePassword"][: gv.sd[u"nst"]]
            commandsAdv[u"deviceKeepState"] = commandsAdv[u"deviceKeepState"][: gv.sd[u"nst"]]

            commandsAdv[u"useLatch"] = commandsAdv[u"useLatch"][: gv.sd[u"nst"]]

            commandsAdv[u"on"] = commandsAdv[u"on"][: gv.sd[u"nst"]]
            commandsAdv[u"off"] = commandsAdv[u"off"][: gv.sd[u"nst"]]

            devicesAccessProtection = devicesAccessProtection[: gv.sd[u"nst"]]

class settings(ProtectedPage):
    """Load an html page for entering advance_control commands"""

    def GET(self):
        check_commands_advance_size()
        return template_render.advance_control(commandsAdv)


class settings_json(ProtectedPage):
    """Returns plugin settings in JSON format"""

    def GET(self):
        check_commands_advance_size()
        web.header(u"Access-Control-Allow-Origin", u"*")
        web.header(u"Content-Type", u"application/json")
        return json.dumps(commandsAdv)


class update(ProtectedPage):
    """Save user input to cli_control.json file"""

    def GET(self):
        global commandsAdv, runValveOnLine

        check_commands_advance_size()

        qdict = web.input()

        for i in range(gv.sd[u"nst"]):
            commandsAdv[u"typeOutput"][i] = qdict[u"typeVal" + str(i)]

            if commandsAdv[u"typeOutput"][i] == "shellyHTTP":
                commandsAdv[u"deviceModel"][i] = qdict[u"shellyModel" + str(i)]
                commandsAdv[u"deviceIP"][i] = qdict[u"shellyIP" + str(i)]

                try:
                    currentPortNumber = int(qdict[u"shellyPort" + str(i)])
                    commandsAdv[u"devicePort"][i] = currentPortNumber
                except ValueError:
                    print("That's not an int!")

                #read user name and password
                commandsAdv[u"deviceUserName"][i] = qdict[u"shellyUserName" + str(i)]
                commandsAdv[u"devicePassword"][i] = qdict[u"shellyUserPwd" + str(i)]

                if qdict[u"protocol" + str(i)] is None:
                    commandsAdv[u"deviceProtocol"][i] = "http"
                else:
                    commandsAdv[u"deviceProtocol"][i] = qdict[u"protocol" + str(i)]

                if (u"useLatch" + str(i)) in qdict:
                    commandsAdv[u"useLatch"][i] = 0
                else:
                    commandsAdv[u"useLatch"][i] = 1

                if (u"deviceKeepState" + str(i)) in qdict:
                    commandsAdv[u"deviceKeepState"][i] = 0
                else:
                    commandsAdv[u"deviceKeepState"][i] = 1

            commandsAdv[u"on"][i] = qdict[u"con" + str(i)]
            commandsAdv[u"off"][i] = qdict[u"coff" + str(i)]

        if u"gpio" in qdict:
            commandsAdv[u"gpio"] = 1
            gv.use_gpio_pins = False
        else:
            commandsAdv[u"gpio"] = 0
            gv.use_gpio_pins = True

        with open(u"./data/advance_control.json", u"w") as f:  # write the settings to file
            json.dump(commandsAdv, f, indent=4)

        # Restart thread to check if is network valves are on-line
        runValveOnLine = False
        threadCheckOnLine.join()
        threadCheckOnLine = Thread(target = run_check_valves_on_line_keep_state)
        threadCheckOnLine.start()

        raise web.seeother(u"/restart")

class valve_status_display(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        check_commands_advance_size()
        return template_render.advance_control_status(commandsAdv)

class check_valve_status(ProtectedPage):
    """Valve status"""

    def GET(self):
        qdict = web.input()

        if "valveId" in qdict:
            valveId = qdict["valveId"]

            if valveId >= 0 and valveId < len(lastTimeValvesOnLine):
                lastSeen = lastTimeValvesOnLine[valveId] # to check id
                timeNow = datetime.datetime.now()

                diff = timeNow - lastSeen

                if diff.seconds > 45:
                    return "red"
                else:
                    return "green"

        return ""
