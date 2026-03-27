class APILoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only log API routes (optional but cleaner)
        if request.path.startswith("/api/"):
            print("\n🔥 API REQUEST")
            print("PATH:", request.path)
            print("METHOD:", request.method)

            if request.method in ["POST", "PUT", "PATCH"]:
                try:
                    print("BODY:", request.body.decode("utf-8"))
                except:
                    print("BODY: (binary/empty)")

        response = self.get_response(request)

        if request.path.startswith("/api/"):
            print("STATUS:", response.status_code)
            print("-------------\n")

        return response