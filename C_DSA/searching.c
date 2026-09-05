#include <stdio.h>
#include "station.h"

/*
   LINEAR SEARCH

   Searches station ID using
   C Linear Search algorithm.
*/

int searchStationById(
    Station stations[],
    int count,
    int stationId
) {
    for (int i = 0; i < count; i++) {

        if (stations[i].station_id == stationId) {
            return i;
        }
    }

    return -1;
}


int main(void) {

    int count;
    int stationId;

    /*
       Flask sends:

       count
       station_id
       station_id
       station_id
       ...
       searched_station_id
    */

    if (scanf("%d", &count) != 1) {
        printf("INVALID_INPUT\n");
        return 1;
    }

    if (count <= 0 || count > 1000) {
        printf("INVALID_COUNT\n");
        return 1;
    }

    Station stations[1000];

    /*
       Read station IDs from Flask
    */

    for (int i = 0; i < count; i++) {

        if (scanf("%d", &stations[i].station_id) != 1) {
            printf("INVALID_INPUT\n");
            return 1;
        }
    }

    /*
       Read ID that user wants to search
    */

    if (scanf("%d", &stationId) != 1) {
        printf("INVALID_INPUT\n");
        return 1;
    }

    /*
       C Linear Search
    */

    int result = searchStationById(
        stations,
        count,
        stationId
    );

    if (result != -1) {

        printf("FOUND\n");
        printf("%d\n", stations[result].station_id);

    } else {

        printf("NOT_FOUND\n");
    }

    return 0;
}