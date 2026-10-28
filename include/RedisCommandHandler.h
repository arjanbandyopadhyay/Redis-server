#ifndef REDIS_COMMAND_HANDLER_H
#define REDIS_COMMAND_HANDLER_H

#include <string>

class RedisCommandHandler {
public:
    RedisCommandHandler();
    // Process a command from a client and return a RESP-formatted response.
    std::string processCommand(const std::string& commandLine);

    // Framing helper for the network layer.
    // Looks at the start of `buf` and returns:
    //   > 0 : byte length of the first COMPLETE command in buf
    //   0   : command not complete yet (wait for more data)
    //   < 0 : protocol error (malformed / oversized input)
    static long commandLength(const std::string& buf);
};

#endif
