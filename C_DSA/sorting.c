#include <stdio.h>
#include "station.h"

void sortByDistance(Station stations[], int count) {

    for (int i = 0; i < count - 1; i++) {

        int min = i;

        for (int j = i + 1; j < count; j++) {

            if (stations[j].distance < stations[min].distance) {
                min = j;
            }
        }

        if (min != i) {

            Station temp = stations[i];
            stations[i] = stations[min];
            stations[min] = temp;
        }
    }
}

int main(void) {

    int count;

    /*
       Flask sends:

       count
       station_id distance
       station_id distance
       ...

       Example:

       7
       101 3.5
       102 5.1
       103 8.2
       104 2.4
       105 4.1
       106 6.3
       107 10.5
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

    for (int i = 0; i < count; i++) {

        if (scanf(
                "%d %f",
                &stations[i].station_id,
                &stations[i].distance
            ) != 2) {

            printf("INVALID_INPUT\n");
            return 1;
        }
    }

    /*
       Selection Sort by distance
    */

    sortByDistance(stations, count);

    /*
       Return sorted station IDs
       and distances to Flask.
    */

    for (int i = 0; i < count; i++) {

        printf(
            "%d|%.1f\n",
            stations[i].station_id,
            stations[i].distance
        );
    }

    return 0;
}