/*
 * ASan/UBSan-enabled native libcurl multi/ABI probe for std.http qualification.
 * Compile with system curl headers and -fsanitize=address,undefined; tests
 * the native library boundary, NOT Python ctypes code or uninstrumented curl.
 */
#include <curl/curl.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CONCURRENCY 8

struct transfer {
  CURL *easy;
  size_t bytes;
  char error[CURL_ERROR_SIZE];
};

static size_t consume(char *data, size_t size, size_t nmemb, void *opaque) {
  struct transfer *t = (struct transfer *)opaque;
  size_t n = size * nmemb;
  (void)data;
  if (size && n / size != nmemb) return 0;
  t->bytes += n;
  return n;
}

static void die(const char *msg) {
  fprintf(stderr, "native probe: %s\n", msg);
  exit(3);
}

static int exercise(const char *url, int rounds) {
  CURLM *multi = curl_multi_init();
  int failures = 0, successful = 0, aborted = 0;
  if (!multi) die("multi_init failed");
  curl_multi_setopt(multi, CURLMOPT_MAX_TOTAL_CONNECTIONS, 4L);
  for (int iteration = 0; iteration < rounds; ++iteration) {
    struct transfer tasks[CONCURRENCY] = {0};
    for (int i = 0; i < CONCURRENCY; ++i) {
      tasks[i].easy = curl_easy_init();
      if (!tasks[i].easy) die("easy_init failed");
      curl_easy_setopt(tasks[i].easy, CURLOPT_URL, url);
      curl_easy_setopt(tasks[i].easy, CURLOPT_NOSIGNAL, 1L);
      curl_easy_setopt(tasks[i].easy, CURLOPT_PROXY, "");
      curl_easy_setopt(tasks[i].easy, CURLOPT_WRITEFUNCTION, consume);
      curl_easy_setopt(tasks[i].easy, CURLOPT_WRITEDATA, &tasks[i]);
      curl_easy_setopt(tasks[i].easy, CURLOPT_ERRORBUFFER, tasks[i].error);
      curl_easy_setopt(tasks[i].easy, CURLOPT_TIMEOUT_MS, 6000L);
      if (curl_multi_add_handle(multi, tasks[i].easy) != CURLM_OK)
        die("add_handle failed");
    }
    /* Cancellation must be safe while other streams share the multi pool. */
    if (iteration % 3 == 0) {
      if (curl_multi_remove_handle(multi, tasks[0].easy) != CURLM_OK)
        die("remove_handle(cancelled) failed");
      curl_easy_cleanup(tasks[0].easy);
      tasks[0].easy = NULL;
      aborted++;
    }
    int active = 1, spins = 0;
    while (active && spins++ < 160) {
      if (curl_multi_perform(multi, &active) != CURLM_OK)
        die("multi_perform failed");
      if (active) {
        int sockets = 0;
        if (curl_multi_poll(multi, NULL, 0, 50, &sockets) != CURLM_OK)
          die("multi_poll failed");
      }
    }
    if (active) die("native transfers failed to quiesce");
    int queued = 0;
    CURLMsg *event;
    while ((event = curl_multi_info_read(multi, &queued))) {
      if (event->msg != CURLMSG_DONE) continue;
      if (event->data.result == CURLE_OK) {
        long status = 0;
        if (curl_easy_getinfo(event->easy_handle, CURLINFO_RESPONSE_CODE, &status) != CURLE_OK
            || status != 200) failures++;
        else successful++;
      } else failures++;
    }
    for (int i = 0; i < CONCURRENCY; ++i) {
      if (!tasks[i].easy) continue;
      if (curl_multi_remove_handle(multi, tasks[i].easy) != CURLM_OK)
        die("remove_handle failed");
      curl_easy_cleanup(tasks[i].easy);
      tasks[i].easy = NULL;
    }
  }
  if (curl_multi_cleanup(multi) != CURLM_OK) die("multi_cleanup failed");
  printf("{\"status\":\"%s\",\"successful\":%d,\"cancelled\":%d,\"failures\":%d,"
         "\"curl_sockaddr_addr_offset\":%zu,\"curl_sockaddr_size\":%zu,"
         "\"curl_off_t_size\":%zu,\"runtime_libcurl_version\":\"%s\"}\n",
         failures ? "failed" : "passed", successful, aborted, failures,
         offsetof(struct curl_sockaddr, addr), sizeof(struct curl_sockaddr),
         sizeof(curl_off_t), curl_version());
  return failures ? 1 : 0;
}

int main(int argc, char **argv) {
  if (argc != 3) die("usage: native_std_http_probe URL ROUNDS");
  int rounds = atoi(argv[2]);
  if (rounds < 1 || rounds > 10000) die("rounds out of range");
  if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) die("global_init failed");
  int status = exercise(argv[1], rounds);
  curl_global_cleanup();
  return status;
}
