#!/usr/bin/python3 -u
# -u unbuffered output to console

# TODO special handling of specifig times at 21:30 and 17:00 half an hour before leave blockBurner!!!

import pdb
# setting isDebug reduces sleep times to 1 sec each
isDebug = False

# ERR: read timeout
# Error in wait, terminating
# Error executing getTempRaumRedSollM1


# FIXME: unrelated but ...
# on Raspi in /etc/systemd/journald.conf set
# ForwardToWall=no as /dev/serial/by-id/usb-1a86... emits
# broadcast messages every 2-4 min
# possible solution on https://bbs.archlinux.org/viewtopic.php?id=193879

import telnetlib
import time
from datetime import datetime
from fritzbox import isAnyoneAtHome, getColdestRoomTemp
#import logging
import json
import signal
import subprocess
import sys
import traceback

#######################################################
#
# Defaults
#
#######################################################

# specify times where boiler has to be able to start (times set at radiators)
# specify a time as "hh:mm"
mustSwitchOnAt = ["17:00", "21:30"]
mustSwitchToday = mustSwitchOnAt.copy()

# interval in seconds to poll Vitodens whether the burner started/ignited:
burnerStartPollTime=20 # seconds

# If burner ignition is detected then poll whether burner is still on
# every 'burnerOnPollTime' seconds.
# Max frequency of burner starts is 4 min
# Niquist: test less than every 2 min => best seems to be e.g. 100 sec
burnerOnPollTime = 100 # seconds

# minimum interval in seconds between two burner ignitions.
# Vitodens has a hard coded blocktime of 4 min, which cannot be changed.
# This leads to over 50 thousands relay switches and gas valve switches
# a year with a relay lifetime of 100000. The purpose of this script
# is to avoid this wear and tear.
# The blocking is achieved by setting the normal room temperature to
# reduced room temperature for the blocking time. 
burnerBlockTime=30 # min

# set temperature to 10 degrees to block the burner.
# NOTE: have seen burner igniting for a few seconds in the night.
#       To rule this out set blocking temperature far lower than reduced temp.
burnerBlockTemp=5
# Viessman blocks the burner for 4 min which still causes 100000 of burner
# starts per year. Blocking it for at least double the time (8 min)
minBlockTime = 8 # min

# The heating has to under perform at least minUnderTempCounter times
# before the slope is raised because the heat has to distribute in the house:
minUnderTempCounter=4
underTempCounter=0

TV_IP = '192.168.29.111'

fn = "/home/ubuntu/logs/vitoconf-" + datetime.now().strftime("%Y-%m-%d_%H.%M") + ".json"
stfn = "/home/ubuntu/logs/vitoconf.json"

# good to choose an even number
hysteresis=1

# maximum temperature below normalTemp allowed
# if lower, blocking burner is exited
maxBelowNorm = 2.0

# if target temperature is not reached and burner 
# goes into blocked state, we allow to get it out
# if the measured temp is maxBelowNor. Then 
# the slope is increased by the temperature difference
# diff:= |current - target|; slope += slope * slopeFactor.
# This applies mainly in the morning if the temperature
# of the house is lower. Remember, the heating curve is
# make and thought to keep the house at the confortable
# level, not to bring it up.
slopeFactor = 0.4
# values allowed down to 0.2 (for underfloor heatings) 
# for conventional it should never be less that 0.7
#minSlope = 0.7
# fixed it to lowest 1.4
minSlope = 1.4
maxSlope = 3.5
# minLevel can be set to -13 but we limit it to 0
minLevel = 0
maxLevel = 40

#######################################################
#
# Globals
#
#######################################################

currentDayOfYear=datetime.now().timetuple().tm_yday

cache_burnerStarts = 0
cache_burnerHours = 0
cache_power = 0
cache_tempReturnFlow = 0
cache_tempInletFlow = 0
cache_outsideTemp = 0
cache_currentBoilerTemp = 0
cache_targetBoilerTemp = 0
cache_normalRoomTemp = 0
cache_reducedRoomTemp = 0
cache_coldestTemp = 0
cache_isNormalRoomTemp = True
cache_isTVon = False
cache_slope = 0
cache_level = 0

reset_slope = None

state_roomTemp = 'N'

isFreezing = False

#######################################################
#
# Debugging
#
#######################################################

if isDebug:
    burnerStartPollTime=1 # seconds
    burnerOnPollTime = 1 # seconds
    burnerBlockTime=1 # min
    minBlockTime = 1 # min

#######################################################
#
# Functions
#
#######################################################

doExit = False
def signalHandler(sig, frame):
    global vitodens, normalTemp, reducedTemp, doExit, minBlockTime, burnerOnPollTime, burnerStartPollTime
    if (doExit):
        return
    print('signalHander')

    minTime = max(burnerStartPollTime, burnerOnPollTime, minBlockTime*60.0/4.0)
    print(nowFormatted(), 'wait approx %.0f seconds for exit - PLEASE WAIT' % (minTime))
    doExit = True

#def plotInBackground():
#    plotThread = threading.Thread(target=plt.show, name='show plot')
#    plotThread.daemon = True
#    plotThread.start()

def sleepMinutes(m):
    """
    Sleeps m minutes while checking exit status every 15 seconds
    """
    global doExit
    #print('sleep')
    m *= 4
    while (not doExit and m != 0):
        m -= 1
        time.sleep(15)
        printEndOfDayInfo()

def connect():
    global vitodens
    print('connect')
    vitodens = telnetlib.Telnet('192.168.29.31', 3002)
    print("telnet 192.168.29.31 successfuly opened")

def reconnectHandler(sig, rec):
    """
    tries to reconnect if vcontrold goes down or if connection
    to remote disconnects
    """
    global vitodens
    print("Connection timed out")
    # FIXME not working throw OS Error
    #       actually there are two connections that may
    #       break down: usb + telnet (second unlikely)
    #       If first breaks down, vcontrold has to be
    #       restarted
    return
    print('reconnectHandler')
    try:
        if (vitodens != None):
            # close connection in case it is still open
            vitodens.close()
    except:
        pass
    connect()

def sendCommand(tc, cmd):
    # FIXME: if USB is disconnected or IR does not have a signal
    #        vcontrold seems to drop the telnet connection.
    #        It continues working however you need a new telnet
    #        connection
    signal.signal(signal.SIGALRM, reconnectHandler)
    #pdb.set_trace()
    try:
        signal.alarm(20)
	# first read and write usually still works without telnet connection
        tc.read_until(b"vctrld>")
        signal.alarm(20)
        tc.write((cmd + '\n').encode())
        signal.alarm(40)
        result = tc.read_until(b'\n').decode('utf-8').strip()
        signal.alarm(0)
        return result
    except EOFError:
        signal.alarm(0) # if read or write throws
        tb = sys.exc_info()[2]
        raise IOError('sendCommand: connection lost').with_traceback(tb)
    except:
        signal.alarm(0) # if read or write throws
        raise IOError('sendCommand: unknown failure')

recursion = 0
def sendGenericCmd(tc, cmd, separator):
    """
    - sends command cmd 
    - error checking of result
    - split off units after separator if separator
    """
    global recursion
    #pdb.set_trace()
    result = sendCommand(tc, cmd)
    if (not result):
        raise EOFError('sendGenericCmd: no result for command %s' % (cmd))
    # for set commands
    if (len(result) == 2 and result == 'OK'):
        return 'OK'
    if ((len(result) >= 4) and (result[:4] == 'ERR:')):
        if ((len(result) >= 6) and (result[5:] == 'read timeout' or result[5:] == 'write timeout')):
            raise IOError("sendGenericCmd: " + result)
        raise ValueError("sendGenericCmd: " + result)
    if ((len(result) >= 4) and (result[:4] == 'SYNC')):
        # if a set command returns SYNC (NOT OK) a repetition of the command
        # usually solved the problem
        #pdb.set_trace()
        if (recursion < 3):
            recursion += 1
            return sendGenericCmd(tc, cmd, separator)
        else:
            errStr = "sendGenericCmd: failed %d times for %s with result %s" % (recursion, cmd, result)
            recursion = 0
            #print(errStr)
            #pdb.set_trace()
            raise ValueError(errStr)
    # remove units or percentage...
    if (separator):
        result = result.split(separator)[0]
    return result

def getInteger(tc, cmd):
    result = sendGenericCmd(tc, cmd, '.')
    if (not result):
        raise ValueError("empty result of command %s" % (cmd))
    if (result.isdigit()):
        return int(result)
    else:
        raise ValueError("%s is not an integer" % (cmd))

def getBurnerStarts(tc):
    global cache_burnerStarts
    cache_burnerStarts = getInteger(tc, 'getBrennerStarts')
    return cache_burnerStarts

def getBurnerHours(tc):
    global cache_burnerHours
    cache_burnerHours = getInteger(tc, 'getBrennerStunden1')
    return cache_burnerHours

def getFloat(tc, cmd):
    result = sendGenericCmd(tc, cmd, ' ')
    if (not result):
        raise ValueError("empty result of command %s" % (cmd))
    try:
        return float(result)
    except ValueError:
        tb = sys.exc_info()[2]
        raise ValueError("%s is not a float" % (cmd)).with_traceback(tb)

def getSlope(tc):
    global cache_slope
    cache_slope = getFloat(tc, 'getNeigungM1')
    return cache_slope

def setSlope(tc, s):
    """
    slope s is put in range and sent to the heating
    s is returned
    """
    global cache_slope, minSlope, maxSlope

    s = getInRange(s, minSlope, maxSlope)
    result = sendGenericCmd(tc, "setNeigungM1 %f" % (s), '')
    if (result != 'OK'):
        raise SystemError(result)
    cache_slope = s
    return s

def getLevel(tc):
    global cache_level
    cache_level = getFloat(tc, 'getNiveauM1')
    return cache_level

def setLevel(tc, s):
    """
    level s is put in range and sent to the heating
    s is returned
    """
    global cache_level, minLevel, maxLevel

    s = getInRange(s, minLevel, maxLevel)
    result = sendGenericCmd(tc, "setNiveauM1 %f" % (s), '')
    if (result != 'OK'):
        raise SystemError(result)
    cache_level = s
    return s

def getInRange(v, minv, maxv):
    if (v < minv):
        return minv
    if (v > maxv):
        return maxv
    return v

def isBurnerOn(tc):
    global cache_power
    cache_power = getFloat(tc, 'getLeistungIst')
    return (cache_power > 0.0)

def getTempRL(tc):
    global cache_tempReturnFlow
    cache_tempReturnFlow = getFloat(tc, 'getTempRL17A')
    return cache_tempReturnFlow

def getTempVL(tc):
    global cache_tempInletFlow
    cache_tempInletFlow = getFloat(tc, 'getTempVListM1')
    return cache_tempInletFlow

def setNormalRoomTemperature(tc, temp):
    """
    temp is put in range and sent to the heating
    temp is returned
    """
    global cache_normalRoomTemp

    #temp = getInRange(temp, max(10, cache_reducedRoomTemp), 25)
    temp = getInRange(temp, 5, 25)
    result = sendGenericCmd(tc, "setTempRaumNorSollM1 %d" % (temp), '')
    if (result != 'OK'):
        raise SystemError(result)
    cache_normalRoomTemp = temp
    return temp

def setReducedRoomTemperature(tc, temp):
    """
    temp is put in range and sent to the heating
    temp is returned
    """
    global cache_reducedRoomTemp

    #pdb.set_trace()
    temp = getInRange(temp, 5, 25)
    result = sendGenericCmd(tc, "setTempRaumRedSollM1 %d" % (temp), '')
    if (result != 'OK'):
        raise SystemError(result)
    cache_reducedRoomTemp = temp
    return temp

def getOutsideTemperature(tc):
    global cache_outsideTemp
    cache_outsideTemp = getInteger(tc, 'getTempA')
    return cache_outsideTemp

def getBoilerCurrentTemp(tc):
    global cache_currentBoilerTemp
    cache_currentBoilerTemp = getInteger(tc, 'getTempKist')
    return cache_currentBoilerTemp

def getBoilerTargetTemp(tc):
    global cache_targetBoilerTemp
    cache_targetBoilerTemp = getInteger(tc, 'getTempKsoll')
    return cache_targetBoilerTemp

def getNormalRoomTemperature(tc):
    global cache_normalRoomTemp
    cache_normalRoomTemp = getInteger(tc, 'getTempRaumNorSollM1')
    return cache_normalRoomTemp

def getReducedRoomTemperature(tc):
    global cache_reducedRoomTemp
    cache_reducedRoomTemp = getInteger(tc, 'getTempRaumRedSollM1')
    return cache_reducedRoomTemp

def blockBurner(tc):
    global burnerBlockTemp, burnerBlockTime, minBlockTime, normalTemp, maxBelowNorm, doExit, reset_slope, isFreezing

    setLevel(tc, 0)

    coldestTemp = getColdestRoomTemp()
    # reduce temperature to avoid new start
    # first reduce the reduced room temperature then normal room temp.
    # otherwise the burner will use the reduced temperature program
    setReducedRoomTemperature(tc, burnerBlockTemp)
    setNormalRoomTemperature(tc, burnerBlockTemp)

    print(nowFormatted(), 'reduced temperature to %d deg. to block burner' % (burnerBlockTemp))
    printInfoTable()

    bs = getBurnerStarts(tc)
    tempNow = getColdestRoomTemp()
    blockTime = 0
    # time based and temp based blocking
    # using coldestTemp <= tempNow goes too often into cycling: exits after burnerBlockTime (8min)
    # then takes pretty much 60sec to ignite the burner and then heats for less and less minutes
    # the more it happens
    #while (not doExit and (coldestTemp <= tempNow or blockTime < burnerBlockTime)):

    # temp involvement results in early breakouts usually after 8min, so it is not doing what I hope
    # temperature has to distribute and therefore the boiler has to wait
    #while (not doExit and (getColdestRoomTemp() >= tempNow - 0.5 or blockTime < burnerBlockTime)):

    # Idea: accept lower temp than normalTemp - it takes time that the temp distributes in the room!
    # If the temp falls however, or is well below normalTemp, then fire
    cTemp = getColdestRoomTemp()
    cmpTemp = cTemp + 1
    while (not doExit and (cTemp >= normalTemp or blockTime < burnerBlockTime)):
        print(nowFormatted(), "WHILE ", cTemp, " cTemp >= normalTemp ", normalTemp, " or ", blockTime, " blockTime < burnerBlockTime")
        sleepMinutes(minBlockTime)
        cTemp = getColdestRoomTemp()
        if (cTemp < tempNow and cmpTemp != cTemp):
            print(nowFormatted(), "coldest room temp fell from %.1f to %.1f while waiting for %d min" % (tempNow, cTemp, blockTime))
            printInfoTable()
            # cmpTemp is the last printed temperature
            cmpTemp = cTemp
        # generally keep burner off check every
        # minBlockTime min whether temp in room fell
        # then set room temp accordingly
        blockTime += minBlockTime
        if (isBurnerRequired()):
            print(nowFormatted(), "burner required")
            break
        # Exception:
        #tempNow = getColdestRoomTemp()
        if (getColdestRoomTemp() <= normalTemp-maxBelowNorm and isAnyoneAtHome()):
            print(nowFormatted(), 'current temp. %.1f deg. is %.1f or more below normalTemp %.1f deg.' % (getColdestRoomTemp(), maxBelowNorm, normalTemp))
            break
        if (tempNow >= normalTemp and reset_slope != None):
            setSlope(tc, reset_slope)
            print(nowFormatted(), 'reset slope to normal %.1f' % (cache_slope))
            printInfoTable()
            reset_slope = None
        getBurnerStarts(tc) # update cache
        # check if burner ignited behind our back while waiting
        if (cache_burnerStarts > bs):
            print(nowFormatted(), 'burner started while being blocked - must be freezing')
            isFreezing = True
            break

    if (blockTime < burnerBlockTime):
        print(nowFormatted(), 'unblocked early after %d min' % (blockTime))
    else:
        print(nowFormatted(), 'burner was blocked for %d min > %d min' % (blockTime, burnerBlockTime))
    printInfoTable()

def getWeekday():
    now = datetime.now()
    #locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    # Pyhton's locale badly working (if at all)
    # on Raspi giving up and using simple 
    # translation table... 
    # Assuming standard locale is en_EN
    # needs to be translated to German (de_DE)
    day = now.strftime("%A")[:2]
    if (day == "Tu"):
        day = "Di"
    elif (day == "We"):
        day = "Mi"
    elif (day == "Th"):
        day = "Do"
    elif (day == "Su"):
        day = "So"
    return day

# works currently only for one configured switch time
# on Vitodens
def isNormalRoomTemp(tc):
    global cache_isNormalRoomTemp
    circuit = 1
    cache_isNormalRoomTemp = True
    result = sendGenericCmd(tc, 'getTimerM%d%s' % (circuit, getWeekday()), '')
    if (not result or len(result) < 20):
        return True
    try:
        hourOn    = int(result[5:7])
        minuteOn  = int(result[8:10])
        hourOff   = int(result[16:18])
        minuteOff = int(result[19:])
    except ValueError:
        tb = sys.exc_info()[2]
        raise ValueError('ValueError in isNormalRoomTemp').with_traceback(tb)
    now = datetime.now()
    todayOn  = now.replace(hour=hourOn, minute=minuteOn, second=0, microsecond=0)
    todayOff = now.replace(hour=hourOff, minute=minuteOff, second=0, microsecond=0)

    if (now > todayOn and now < todayOff):
        return True
    cache_isNormalRoomTemp = False
    return False

def isBurnerRequired():
    """
    The heating in certain rooms stays off for a long time and then the room needs to be heat up.
    E.g. the children's bathroom. This must be on time and an unfortunate burner block may delay this
    by a long time.
    """
    global mustSwitchToday
    now = datetime.now()
    #pdb.set_trace()
    for t in mustSwitchToday:
        h = int(t.split(':')[0])
        m = int(t.split(':')[1])
        todayOn  = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if (now >= todayOn):
            # a once used time is removed from the array
            mustSwitchToday.remove(t)
            return True
    return False

def isTVon():
    global cache_isTVon
    # ping: >/dev/null: Temporary failure in name resolution
    # FIXME: consider change to next day... not working as heating goes into reduced mode
    #        ==> extend normal temp mode time
    # ping == 0 => OK, ping == 2 => no response, ping == 3 => failed
    # Fails with above message: isTVon = (0 == subprocess.call(['ping', '-q', '-c', '3', '192.168.29.111', '>/dev/null']))
    cache_isTVon = (0 == subprocess.call(['ping', '-q', '-c', '3', TV_IP], stdout=subprocess.PIPE))
    if (cache_isTVon):
        print(nowFormatted(), 'NIGHT TIME BUT TV STILL ON')
        return True
    else:
        return False

def nowFormatted():
    now = datetime.now()
    return now.strftime("%H:%M:%S")

# Returns the temp difference between the coldest room and the target temperature
# for the current daytime (time of reduced or normal temperature).
# Negative means the coldest room is below the target temperature.
def getDiffCurrTarget(diff, currentSetTemp):
    global hysteresis, cache_coldestTemp
    cache_coldestTemp = getColdestRoomTemp()
    if (cache_coldestTemp == 0):
        return 0

    diff  = 0 # to be subtracted from currentSetTemp
    diffL = cache_coldestTemp -  currentSetTemp
    diffH = cache_coldestTemp - (currentSetTemp + hysteresis)
    if (diffH > 0):
        diff = diffH
    elif (diffL < 0):
        diff = diffL
    return diff

def resetHeating(tc):
    global normalTemp, reducedTemp, cache_level

    setNormalRoomTemperature(tc, normalTemp)
    setReducedRoomTemperature(tc, reducedTemp)
    # add slope from json
    setLevel(tc, 0)
    setSlope(tc, 1.4)


def setRoomTemp(tc):
    """
    setRoomTemp is called while burner is blocked or while waiting for 
    new burner start. The temperature can only fall during this time.
    """ 
    global normalTemp, reducedTemp, nTemp, rTemp, nDiff, rDiff, slopeFactor
    global minSlope, maxSlope, cache_slope, reset_slope, state_roomTemp, cache_level
    #pdb.set_trace()
    if (not isAnyoneAtHome()):
        if state_roomTemp != 'E':
            if (isNormalRoomTemp(tc)): # during daytime
                temp = burnerBlockTemp
            else: # at night rely on heating
                temp = reducedTemp
            print(nowFormatted(), 'nobody home set normal and reduced temp. to %d deg.' % (temp))
            printInfoTable()
            setReducedRoomTemperature(tc, temp)
            setNormalRoomTemperature(tc, temp)
        state_roomTemp = 'E' # empty house
    elif (isNormalRoomTemp(tc)): # daytime
        state_roomTemp = 'N'
        nDiff = getDiffCurrTarget(nDiff, normalTemp)
        # after a steep temp. decline raise slope temporarily until next blockBurner() 
        if (nDiff <= -0.5):
            print(nowFormatted(), 'current day temp. %.1f is %.1f below normal room temp. %.1f' % (cache_coldestTemp, -nDiff, normalTemp))
            # diff to flow temperature:
            # outside  10 inside 20 => diff 10 => ca 17 (1.4 slope) ca 29 (2.2 slope)
            # outside   0 inside 20 => diff 20 => ca 30 (1.4 slope) ca 49 (2.2 slope)
            # outside -10 inside 20 => diff 30 => ca 42 (1.4 slope) ca 67 (2.2 slope)
            keep = cache_level
            cache_level = int(((-nDiff) * cache_slope * 1.5) + 0.5)
            setLevel(tc, cache_level)
            print(nowFormatted(), 'temporary level increase to %d' % (cache_level))
            cache_level = keep
            #if (reset_slope == None):
            #    reset_slope = cache_slope
            #    cache_slope += (-nDiff) * slopeFactor
            #    setSlope(tc, cache_slope)
            #    print(nowFormatted(), 'temporary slope increase to %.1f' % (cache_slope))
            # double the temp increase to boost more
            #nTemp = getInRange(nTemp - nDiff, min(10, rTemp), 25)
            printInfoTable()
	# correct in both directions
        nTemp = getInRange(nTemp - nDiff, min(10, rTemp), 25)
        setNormalRoomTemperature(tc, nTemp - nDiff + hysteresis)
        setReducedRoomTemperature(tc, rTemp)
    elif (isTVon()): 
        state_roomTemp = 'T'
        nDiff = getDiffCurrTarget(nDiff, normalTemp)
        if (nDiff < 0):
            print(nowFormatted(), 'eve TV temp. fell by %.1f' % (-nDiff))
	# correct in both directions
        nTemp = getInRange(nTemp - nDiff, min(10, rTemp), 25)
        # FIXME: do the same tmep slope increase if needed
        setNormalRoomTemperature(tc, nTemp - nDiff + hysteresis)
        setReducedRoomTemperature(tc, nTemp - nDiff + hysteresis)
    else: # (nighttime and TV off) 
        state_roomTemp = 'R' # reduced temp
        rDiff = getDiffCurrTarget(rDiff, reducedTemp)
        if (rDiff < 0):
            print(nowFormatted(), 'nighttime temp. fell by %.1f while blocked or wait for burner' % (-rdiff))
	# correct in both directions
        #setNormalRoomTemperature(tc, normalTemp)
        setReducedRoomTemperature(tc, rTemp - rDiff)

def waitForBurnerOff(tc):
    global burnerOnPollTime, doExit, cache_level

    print(nowFormatted(), 'waitForBurnerOff')
    VL = []
    RL = []
    timestamps = []
    i = 0
    # wait for it to stop
    while (not doExit and isBurnerOn(tc) and isAnyoneAtHome()):
        # boiler burns sometimes for 30-40 min
        # we don't want that if we just left home (isAnyoneAtHome())
        VL.append(getTempVL(tc))
        RL.append(getTempRL(tc))
        timestamps.append(i * burnerOnPollTime)
        i += 1
        #print(nowFormatted(), 'wait %d seconds for boiler to turn off' % (burnerOnPollTime))
        #printInfoTable()
        time.sleep(burnerOnPollTime)
    if (not isAnyoneAtHome()):
        print(nowFormatted(), 'at this minute everyone left the house - switch burner off')
    # reset level
    setLevel(tc, 0)
    #writeVlRlData(VL, RL, timestamps)
	
#def writeVlRlData(VL, RL, timestamps):
#    obj = {
#        'VL':    VL,
#        'RL':    RL,
#        'index': timestamps
#        }
#    fn = "data/" + datetime.now().strftime("%H_%M_%S") + ".json"
#    with open(fn, "w") as wfile:
#        wfile.write(json.dumps(obj))

def printInfoTable():
    global rTemp, nTemp, vitodens, reducedTemp, normalTemp, hysteresis
    global cache_power, cache_tempReturnFlow 
    global cache_tempInletFlow, cache_outsideTemp, cache_currentBoilerTemp 
    global cache_targetBoilerTemp, cache_normalRoomTemp, cache_reducedRoomTemp
    global cache_coldestTemp, cache_slope, state_roomTemp
    # TO  = Outside temperature
    # TII = coldest house temperature (Livingroom current temp)
    # TIT = Livingroom target temp - R/N for reduced / normal
    # D  = diff
    # H  = True if someone is at home
    # TBI = Boiler Temperature is (current)
    # TBT = Target Boiler Temperature 
    lower = int(normalTemp)
    upper = int(normalTemp + hysteresis)
    isHome = isAnyoneAtHome()
    if (not isHome):
        lower = int(reducedTemp)
        upper = int(reducedTemp + hysteresis)
        temp  = rTemp
    elif (cache_isNormalRoomTemp): # daytime
        temp  = nTemp
    elif (cache_isTVon): 
        temp  = nTemp
    else: # (nighttime and TV off) 
        lower = int(reducedTemp)
        upper = int(reducedTemp + hysteresis)
        temp  = rTemp
    #print('         TO %.1f  TI %s %.1f(%.1f) [%d; %d]   ND %.1f   RD %.1f   H %d   TBI %d   TBT %d' %
    #      (getOutsideTemperature(vitodens), state_roomTemp, cache_coldestTemp, temp, lower, upper, -nDiff, -rDiff, isHome, getBoilerCurrentTemp(vitodens), getBoilerTargetTemp(vitodens)))
    print(nowFormatted(),
 	'TO %f  TI %s %.1f(%.1f) [%d; %d]   ND %.1f   RD %.1f   H %d   TBI %d   TBT %d   S %.1f   L %d' %
        (getOutsideTemperature(vitodens), state_roomTemp, cache_coldestTemp, temp, lower, upper, -nDiff, -rDiff, isHome, getBoilerCurrentTemp(vitodens), getBoilerTargetTemp(vitodens), getSlope(vitodens), getLevel(vitodens)))

def isSameDay():
    global currentDayOfYear
    try:
        if (datetime.now().timetuple().tm_yday == currentDayOfYear): 
            #print('same day')
            return True
    except:
        print('ERROR printEndOfDayInfo: failure')
        return True

    currentDayOfYear=datetime.now().timetuple().tm_yday
    return False

def printEndOfDayInfo():
    global cache_burnerHours, cache_burnerStarts, currentDayOfYear
    global burnerStarts, burnerHours
    global mustSwitchOnAt, mustSwitchToday

    if (isSameDay()):
       return

    mustSwitchToday = mustSwitchOnAt.copy()

    # AOT = average on time
    # NOS = number of burner starts in 24 hours
    # BH  = burner hours
    getBurnerStarts(vitodens)
    getBurnerHours(vitodens)
    if (cache_burnerStarts == burnerStarts):
        aot = 0
    else:
        aot = (cache_burnerHours - burnerHours) / (cache_burnerStarts - burnerStarts)
    str = 'AOT: %f    NOS: %d   BH: %d' % (aot, (cache_burnerStarts - burnerStarts), (cache_burnerHours - burnerHours))
    now = datetime.now()
     
    print(now.strftime("%Y/%m/%d"), str)
    #with open('/var/log/burner.log', 'a') as f:
    #    f.write(datetime.now().strftime("%d/%m/%y"), str)
    burnerStarts = cache_burnerStarts
    burnerHours  = cache_burnerHours

def writeConfig(tc):
    global normalTemp, reducedTemp, nTemp, rTemp, fn
    print(nowFormatted(), 'getting burner hours - PLEASE WAIT')
    bh = getBurnerHours(tc)
    print(nowFormatted(), 'getting burner starts - PLEASE WAIT')
    bs = getBurnerStarts(tc)
    print(nowFormatted(), 'getting slope - PLEASE WAIT')
    sl = getSlope(tc)
    l = getLevel(tc)
    data = {
        "timestamp": datetime.now().strftime("%Y/%m/%d-%H:%M:%S"),
        "Brennerstunden": bh,
        "Brennerstarts": bs, 
        "Neigung": sl,
        "Niveau" : l,
        "Normaltemp": normalTemp,
        "Reduziertetemp": reducedTemp,
        "SteuerNormaltemp": nTemp,
        "SteuerReduziertetemp": rTemp
    }
    print(nowFormatted(), 'writing file - PLEASE WAIT')
    with open(fn, "w") as wfile:
        wfile.write(json.dumps(data, indent=4))

config = None
def readConfig(tc):
    global normalTemp, reducedTemp, nTemp, rTemp, stfn, config
    print("Try opening ", stfn)
    with open(stfn, 'r') as rfile:
        config = json.load(rfile)
    print("Read configuration from file")
    setSlope(tc, config["Neigung"])
    setLevel(tc, 0) # set null for now config["Niveau"])
    normalTemp = config["Normaltemp"]
    reducedTemp = config["Reduziertetemp"]
    nTemp = config["SteuerNormaltemp"]
    rTemp = config["SteuerReduziertetemp"]


isBurnerStateChanged = True
def loop():
    global nDiff, rDiff, rTemp, nTemp, vitodens, reducedTemp, normalTemp, minSlope, maxSlope
    global stateSomeoneHome, currentCount, burnerBlockTime
    global cache_slope, isBurnerStateChanged
    global minUnderTempCounter, underTempCounter, isDebug, isFreezing

    #print('loop')
    printEndOfDayInfo()
    #print(".")

    if (isDebug):
        pdb.set_trace()
            
    setRoomTemp(vitodens)
    # give burner time to ignite
    time.sleep(burnerStartPollTime)

    # FIXME: during WW and Party do not block burner
    newCount = getBurnerStarts(vitodens)
    # isBurnerOn should really be enough: if (newCount > currentCount or isBurnerOn(vitodens)):
    if (isBurnerOn(vitodens)):
        isBurnerStateChanged = True
        # if the burner ignited in during blockBurner (which we cannot avoid)
        # then the burner was already on and we should not mislead by saying it ignited
        if (not isFreezing):
            print(nowFormatted(), 'boiler recently ignited')
        isFreezing = False
        printInfoTable()
        waitForBurnerOff(vitodens)

        # after the burner finished heating the house 
        # if the temperature is smaller than the target
        # then we increase the slope by 0.1
        coldestTemp = getColdestRoomTemp()
        # it takes time for the warmth to distribute, so the 
        # "under temperature" at daytime has to happen at least
        # minUnderTempCounter times:
        if (isNormalRoomTemp(vitodens) and (coldestTemp <= normalTemp-0.5)):
            # give the warmth to spread around the room => underTempCounter
            # 1.5 degrees increase take about 1 hour 40 to spread through the living room
            underTempCounter += 1
            if (underTempCounter > minUnderTempCounter): 
                underTempCounter=0
                cache_slope += 0.1
                setSlope(vitodens, cache_slope)
                print(nowFormatted(), 'increased slope to %.1f' % (cache_slope))
                # reset nTemp and rTemp
                rTemp = getInRange(reducedTemp, 10, 25)
                nTemp = getInRange(normalTemp, max(10, rTemp), normalTemp)
                printInfoTable()
        if (isNormalRoomTemp(vitodens) and (coldestTemp > normalTemp)):
            cache_slope -= 0.1
            setSlope(vitodens, cache_slope)
            print(nowFormatted(), 'decreased slope to %.1f' % (cache_slope))
            rTemp = getInRange(reducedTemp, 10, 25)
            nTemp = getInRange(normalTemp, max(10, rTemp), normalTemp)
            printInfoTable()

        blockBurner(vitodens)

        print(nowFormatted(), 'wait for new burner start')
        printInfoTable()
    else:
        if isBurnerStateChanged:
           print(nowFormatted(), 'wait for burner start')
           printInfoTable()
        isBurnerStateChanged = False

    currentCount = newCount
    #print(".", end='')

if __name__ == '__main__':

    # exit gracefully on Ctrl-C
    signal.signal(signal.SIGINT, signalHandler)

    #pdb.set_trace()
    vitodens = None
    while (not doExit and not vitodens):
        try:
            connect()
            getOutsideTemperature(vitodens)
        except ValueError:
            if (vitodens != None):
                print("telnet failed - reconnecting 15sec")
                # close connection in case it is still open
                vitodens.close()
        except:
            print("telnet failed - waiting 15sec")
            vitodens = None
        time.sleep(15)


    #logging.basicConfig(filename='vitodens.log', level=logging.DEBUG)
    #logging.debug("test")


    # for testing set them as fixed
    #setNormalRoomTemperature(vitodens, 17)
    #setReducedRoomTemperature(vitodens, 13)

    burnerStarts = getBurnerStarts(vitodens)
    currentCount = burnerStarts
    burnerHours  = getBurnerHours(vitodens)
    
    #pdb.set_trace()

    try: 
        print("Try reading config file")
        readConfig(vitodens)
    except (FileNotFoundError, KeyError, json.decoder.JSONDecodeError): 
        print("readConfig failed - reading from Vitodens")
        getSlope(vitodens)
        getLevel(vitodens)
        reducedTemp = getReducedRoomTemperature(vitodens)
        normalTemp = getNormalRoomTemperature(vitodens)
        rTemp = getInRange(reducedTemp, 10, 25)
        nTemp = getInRange(normalTemp, max(10, rTemp), normalTemp)

    stateSomeoneHome = not isAnyoneAtHome()
    
    # simple command line processing:
    l = len(sys.argv)
    if (l > 5):
        print('Usage:  startLimiter.py <normal temp> <reduced temp> <hysteresis> <block time>')
        sys.exit(0)
    if (l == 5):
        burnerBlockTime = int(sys.argv[4])
    if (l >= 4):
        hysteresis = int(sys.argv[3])
    if (l >= 3):
        reducedTemp = int(sys.argv[2])
    if (l >= 2):
        normalTemp = int(sys.argv[1])
    
    print("Slope: ", config["Neigung"])
    print("Normal Temp.: ", config["Normaltemp"])
    print("Reduced Temp.: ", config["Reduziertetemp"])
    print("Target Normal Temp: ", config["SteuerNormaltemp"])
    print("Target Normal Temp: ", config["SteuerReduziertetemp"])

    nDiff = getDiffCurrTarget(0, normalTemp)
    rDiff = getDiffCurrTarget(0, reducedTemp)

    print('starting to loop')
    while (not doExit):
        #loop()
    
        try:
            loop()
        except:
            resetHeating(vitodens)
            tb = sys.exc_info()[2]
            e = sys.exc_info()[0]
            print("ERROR: %s" % (e))
            print(tb)
            print(traceback.format_exc())

    # reset values from before start of the script
    print(nowFormatted(), 'resetting normal room temperature - PLEASE WAIT')
    setNormalRoomTemperature(vitodens, normalTemp)
    print(nowFormatted(), 'resetting night room temperature - PLEASE WAIT')
    setReducedRoomTemperature(vitodens, reducedTemp)

    # reset slope in case it is temporary set
    setSlope(vitodens, cache_slope)
    writeConfig(vitodens)

    # FIXME: seems not to exist - try out in shell
    #vitodens.get_socket().shutdown(socket.SHUT_WR)
    #data = vitodens.read_all()
    vitodens.close()
