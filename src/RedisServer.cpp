#include "../include/RedisServer.h"
#include "../include/RedisCommandHandler.h"
#include "../include/RedisDatabase.h"

#include <iostream>
#include <sys/socket.h>
#include <unistd.h>
#include <netinet/in.h>
#include <vector>
#include <thread>
#include <cstring>
#include <signal.h>

// Global pointer for signal handling
static RedisServer* globalServer = nullptr;

void signalHandler(int signum) {
    if (globalServer) {
        std::cout << "\nCaught signal " << signum << ", shutting down...\n";
        globalServer->shutdown();
    }
    exit(signum);
}

void RedisServer::setupSignalHandler() {
    signal(SIGINT, signalHandler);
}

RedisServer::RedisServer(int port) : port(port), server_socket(-1), running(true) {
    globalServer = this;
    setupSignalHandler();
}

void RedisServer::shutdown() {
    running = false;
    if (server_socket != -1) {
        // Before shutdown, persist the database
        if (RedisDatabase::getInstance().dump("dump.my_rdb"))
            std::cout << "Database Dumped to dump.my_rdb\n";
        else 
            std::cerr << "Error dumping database\n";
        close(server_socket);
    }
    std::cout << "Server Shutdown Complete!\n";
}

void RedisServer::run() {
    server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (server_socket < 0) {
        std::cerr << "Error Creating Server Socket\n";
        return;
    }

    int opt = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in serverAddr{};
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(port);
    serverAddr.sin_addr.s_addr = INADDR_ANY;

    if (bind(server_socket, (struct sockaddr*)&serverAddr, sizeof(serverAddr)) < 0) {
        std::cerr << "Error Binding Server Socket\n";
        return;
    }

    if (listen(server_socket, 10) < 0) {
        std::cerr << "Error Listening On Server Socket\n";
        return;
    } 

    std::cout << "Redis Server Listening On Port " << port << "\n";

    std::vector<std::thread> threads;
    RedisCommandHandler cmdHandler;

    while (running) {
        int client_socket = accept(server_socket, nullptr, nullptr);
        if (client_socket < 0) {
            if (running) 
                std::cerr << "Error Accepting Client Connection\n";
            break;
        }

        threads.emplace_back([client_socket, &cmdHandler](){
            char buffer[4096];
+            std::string pending;       
+            bool alive = true;
            while (true) {
                int bytes = recv(client_socket, buffer, sizeof(buffer), 0);
                if (bytes <= 0) break;
                pending.append(buffer, bytes);
+
+                std::string response;
+                while (true) {
+                    long len = RedisCommandHandler::commandLength(pending);
+                    if (len == 0) break;                       
+                    if (len < 0) {                            
+                        response += "-ERR Protocol error\r\n";
+                        alive = false;
+                        break;
+                    }
+                    response += cmdHandler.processCommand(pending.substr(0, len));
+                    pending.erase(0, len);
+                }
+
+                size_t sent = 0;
+                while (sent < response.size()) {
+                    ssize_t n = send(client_socket, response.data() + sent,
+                                     response.size() - sent, MSG_NOSIGNAL);
+                    if (n <= 0) { alive = false; break; }
+                    sent += static_cast<size_t>(n);
+                }
             }
             close(client_socket);
        });
    }
    
    for (auto& t : threads) {
        if (t.joinable()) t.join();
    }

    
    if (RedisDatabase::getInstance().dump("dump.my_rdb"))
        std::cout << "Database Dumped to dump.my_rdb\n";
    else 
        std::cerr << "Error dumping database\n";
}
