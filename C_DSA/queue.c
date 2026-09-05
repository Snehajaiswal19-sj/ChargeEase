#include <stdio.h>
#include "station.h"

#define MAX_QUEUE 100

int main(void) {
    int stationId, waiting;
    if (scanf("%d %d", &stationId, &waiting) != 2) {
        printf("INVALID_INPUT\n"); return 1;
    }
    if (waiting < 0) waiting = 0;
    if (waiting > MAX_QUEUE) waiting = MAX_QUEUE;
    printf("QUEUE_OK\n%d\n%d\n", stationId, waiting);
    return 0;
}
