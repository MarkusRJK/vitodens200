# Vitodens 200 Burner Restrictor
Vitodens 200 with weather compensation unit uses a graph to determine the flow temperature from the outside temperature.
The idea is to limit the flow temperature instead of restricting the flows by thermostats at each heater. Restricting the flows in a heating system requires more pump energy for the circulation. Vitodens with weather compensation uses no inside thermostat and determines the necessary flow temperature from the graph and two inputs, the slope and the level of the graph, which depend on the house type.

## Other Properties of Vitodens 200
* between two ignitions of the burner is a minimum interval of 4 minutes which cannot be changed
* the hysteresis between the flow and return flow cannot be set

## High Burner Ignitions
What you see happening is: the controls magically determine that the burner needs to ignite. A certain high gas input has to be provided to the ignition, then the burner reduces. Often the flow temperature quickly rises because the return flow is still hot. The burner switches off after less than 2 minutes. Then it is blocked for 4 minutes until the game starts again. After a few such cycles it eventually will happen that the return flow is cold enough and the burner will fire for longer. This easily leads to 150 burner starts within 18 hours causing damage to the gas valve and the relays on the board. With the needed high gas input for the ignition and the short circulation of less than 2 minutes, most of the heat does not get to heaters. Viessmann may tell you to balance the heating system, but that will not solve all issues.

## Design Issue
The issue with this design is:
* fixed slope and level does not work for badly insulated houses, e.g. in Ireland and England, houses are badly insulated and there are drafts in the house. I.e. with strong winds hotter flow needs to be provided, i.e. there needs to be a set of slope/level for windy days
* missing thermostat in the house (all depends on the chosen slope that is determined by experiment)
* too short time between ignitions (the heat needs time to distribute)
* the graph determines the flow temperature to keep the inside temperature at e.g. 20°C for a specific outside temperature. There is no information what the flow should be if the temperature ever fell below 20°C.
* after a reduced temperature (e.g. in the night) the flow temperature must be increased until the 20°C are reached.

# Start Limiter
The start limiter reduces the number of burner starts to 15 and less and the average burner on time is 20 to 50 minutes.
All heating controls remain working as before. 
