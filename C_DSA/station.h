#ifndef STATION_H
#define STATION_H

typedef struct {
    int station_id;
    char name[100];
    char location[100];
    char charger_type[50];
    int total_slots;
    int available_slots;
    int waiting_vehicles;
    float distance;
} Station;

#endif
