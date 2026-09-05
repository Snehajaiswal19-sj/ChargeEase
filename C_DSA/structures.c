#include <stdio.h>
#include "station.h"

void printStation(const Station *s) {
    printf("%d|%s|%s|%s|%d|%d|%d|%.1f\n", s->station_id, s->name, s->location,
           s->charger_type, s->total_slots, s->available_slots, s->waiting_vehicles, s->distance);
}
