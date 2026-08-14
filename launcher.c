/*
 * Native launcher for the scheduled LaunchAgent.
 *
 * macOS's TCC policy refuses to show the Calendar access prompt when the
 * "responsible" process resolves to a bare shell interpreter or a raw
 * python binary. Wrapping the Python invocation in a compiled binary living
 * inside a proper .app bundle (with an Info.plist declaring
 * NSCalendarsUsageDescription) gives TCC a real app to attribute the
 * request and prompt for.
 *
 * Usage: SoccerCalendar <python-interpreter-path> <script-path>
 */

#include <unistd.h>
#include <sys/wait.h>

int main(int argc, char *argv[]) {
    if (argc < 3) {
        return 64; /* EX_USAGE */
    }

    pid_t pid = fork();
    if (pid == 0) {
        execl(argv[1], "python", argv[2], (char *)NULL);
        _exit(127);
    } else if (pid > 0) {
        int status;
        waitpid(pid, &status, 0);
        return WEXITSTATUS(status);
    }
    return 1;
}
