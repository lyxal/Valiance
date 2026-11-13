// A second, independent HTTP/2 client (Go net/http) for the real TLS fixture.
// Run: go run scripts/http2_go_interop.go https://127.0.0.1:<port>/
// This does NOT replace h2spec's negative wire-protocol corpus.
package main

import (
    "bytes"
    "context"
    "crypto/tls"
    "fmt"
    "io"
    "net/http"
    "os"
    "sync"
    "time"
)

func main() {
    if len(os.Args) != 2 {
        fmt.Fprintln(os.Stderr, "usage: http2_go_interop <https-url>")
        os.Exit(2)
    }
    transport := &http.Transport{
        TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, // ephemeral self-signed test cert
        ForceAttemptHTTP2: true,
        MaxIdleConns: 32,
        MaxIdleConnsPerHost: 8,
        MaxConnsPerHost: 4,
    }
    defer transport.CloseIdleConnections()
    client := &http.Client{Transport: transport, Timeout: 10 * time.Second}
    const parallel, iterations = 12, 10
    ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
    defer cancel()
    var group sync.WaitGroup
    problems := make(chan error, parallel*iterations)
    for worker := 0; worker < parallel; worker++ {
        group.Add(1)
        go func(worker int) {
            defer group.Done()
            for step := 0; step < iterations; step++ {
                method := http.MethodGet
                var body io.Reader
                if (worker+step)%2 == 0 {
                    method = http.MethodPost
                    size := 1 + ((worker+1)*(step+1)*7919)%196608
                    payload := bytes.Repeat([]byte{byte(worker+1)}, size)
                    body = bytes.NewReader(payload)
                }
                req, err := http.NewRequestWithContext(ctx, method, os.Args[1], body)
                if err != nil { problems <- err; return }
                resp, err := client.Do(req)
                if err != nil { problems <- err; return }
                content, readErr := io.ReadAll(resp.Body)
                closeErr := resp.Body.Close()
                if readErr != nil { problems <- readErr; return }
                if closeErr != nil { problems <- closeErr; return }
                if resp.ProtoMajor != 2 || resp.StatusCode != 200 || string(content) != "valiance-h2-ok" {
                    problems <- fmt.Errorf("worker %d iteration %d protocol=%s status=%d body=%q", worker, step, resp.Proto, resp.StatusCode, content)
                    return
                }
            }
        }(worker)
    }
    group.Wait()
    close(problems)
    count := 0
    for err := range problems {
        fmt.Fprintln(os.Stderr, err)
        count++
    }
    if count > 0 { os.Exit(1) }
    fmt.Printf("Go net/http HTTP/2 interoperability passed: %d GET/POST requests over parallel TLS sessions\n", parallel*iterations)
}
