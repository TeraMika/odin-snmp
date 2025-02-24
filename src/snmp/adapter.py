import logging
import asyncio
import time
import json
from concurrent import futures

from tornado.ioloop import PeriodicCallback
from tornado.concurrent import run_on_executor

from odin.adapters.adapter import ApiAdapterResponse, request_types, response_types
from odin.adapters.async_adapter import AsyncApiAdapter
from odin.adapters.async_parameter_tree import AsyncParameterTree
from odin.adapters.base_parameter_tree import ParameterTreeError
from odin.util import decode_request_body, run_in_executor

from puresnmp import Client, V2C, PyWrapper, ObjectIdentifier as OID

class SnmpAdapter(AsyncApiAdapter):
    """d"""

    # Thread executor used for background tasks
    executor = futures.ThreadPoolExecutor(max_workers=2)

    def __init__(self, **kwargs):
        """Intialize the AsyncDummy Adapter object.

        This constructor initializes the AsyncDummyAdapter object, including configuring an async
        parameter tree with accessors triggering simulated long-running task (sleep), the duration 
        and implemntation of which can be selected by configuration parameters.
        """
        super(SnmpAdapter, self).__init__(**kwargs)

        logging.getLogger("puresnmp").setLevel(logging.WARNING)

        # Parse the configuration options to determine the sleep duration and if we are wrapping
        # a synchronous sleep in a thread pool executor.
        self.bg_task_enable = bool(int(self.options.get('bg_task_enable', 0)))
        self.bg_task_interval = int(self.options.get('bg_task_interal', 5))
        addresses_json = str(self.options.get('addresses_json', 'test/config/clients.json'))

        self.client = PyWrapper(Client("devpdu01.aeg.lan", V2C("public")))

        # Initialise counters for the async and sync tasks and a trivial async read/write parameter
        self.sync_task_count = 0
        self.async_task_count = 0
        self.async_rw_param = 1234

        self.results = []
        self.clients = {}

        self.create_clients(addresses_json=addresses_json)

        self.param_tree = AsyncParameterTree({
            'sync_task_count': (lambda: self.sync_task_count, None),
            'async_task_count': (lambda: self.async_task_count, None),
            'set_bg_task_enable': (lambda: self.bg_task_enable, self.set_bg_task_enable),
            'results': (lambda: self.results, None),
            'async_rw_param': (self.get_async_rw_param, self.set_async_rw_param)
        })

        # Create the thread pool executor
        self.executor = futures.ThreadPoolExecutor()


        if self.bg_task_enable:
            self.start_bg_task()

    def create_clients(self, addresses_json):
        try:
            # Load clients and desired addresses from JSON
            with open(addresses_json, "r") as config_file:
                self.clients = json.load(config_file)

            for client in self.clients.values():
                logging.debug(f"address: {client['address']}")
                client['client'] = PyWrapper(Client(
                    client['address'], V2C("public")
                ))

                client['outputs'] = {}
                for request in client['requests']:
                    client['outputs'][request] = []

            logging.debug(f"clients: {self.clients}")

        except FileNotFoundError as e:
            logging.warning(f"Could not load file: {e}")
        except json.JSONDecodeError as e:
            logging.warning(f"Error decoding clients file: {e}")

        self.addresses_json = addresses_json
        logging.debug(f"Successfully loaded client info from file {addresses_json}.")

    def set_bg_task_enable(self, enable):
        """Stop or start the background task."""
        enable = bool(enable)

        if enable != self.bg_task_enable:
            if enable:
                self.start_bg_task()
            else:
                self.stop_bg_task()

    def start_bg_task(self):
        """Start the background task in a callback."""
        logging.debug(
            "Launching background tasks with interval %.2f secs", self.bg_task_interval
        )

        self.bg_task = PeriodicCallback(self.bg_task, self.bg_task_interval)
        self.bg_task.start()
        self.bg_task_enable = True

    def stop_bg_task(self):
        """Stop the background task."""
        self.bg_task.stop()
        # self.bg_task = None
        self.bg_task_enable = False

    async def bg_task(self):
        """Simulate an asynchronous long-running task.

        This method simulates an async long-running task by performing an asyncio sleep for the
        configured duration.
        """
        for client in self.clients.values():
            for request in client['requests']:
                output = await client['client'].get(request)
                client['outputs'][request].append(output)

        output = await self.client.get("1.3.6.1.4.1.318.1.1.12.2.3.1.1.2.1")
        self.results.append(output)
        logging.debug(f"output: {output}")
        self.async_task_count += 1
        await asyncio.sleep(self.bg_task_interval)

    def get_wrap_sync_sleep(self):
        """Simulate a sync parameter access.

        This method demonstrates a synchronous parameter access, returning the the current value
        of the wrap sync sleep parameter passed into the adapter as an option.
        """
        logging.debug("Getting wrap sync sleep flag: %s", str(self.wrap_sync_sleep))
        return self.wrap_sync_sleep

    async def get_async_rw_param(self):
        """Get the value of the async read/write parameter.

        This async method returns the current value of the async read/write parameter.

        :returns: current value of the async read/write parameter.
        """
        await asyncio.sleep(0)
        return self.async_rw_param

    async def set_async_rw_param(self, value):
        """Set the value of the async read/write parameter.

        This async updates returns the current value of the async read/write parameter.

        :param: new value to set parameter to
        """
        await asyncio.sleep(0)
        self.async_rw_param = value

    # Adapter activities

    async def cleanup(self):
        """Clean up the adapter.

        This dummy method demonstrates that async adapter cleanup can be performed asynchronously.
        """
        logging.debug("SnmpAdapter cleanup called, stopping task.")
        self.stop_bg_task()
        await asyncio.sleep(0)

    @response_types('application/json', default='application/json')
    async def get(self, path, request):
        """Handle an HTTP GET request.

        This method handles an HTTP GET request, returning a JSON response. The parameter tree
        data at the specified path is returned in the response. The underlying tree has a mix of
        sync and async parameter accessors, and GET requests simulate the concurrent operation of
        async adapters by sleeping for specified periods where appropriate.

        :param path: URI path of request
        :param request: HTTP request object
        :return: an ApiAdapterResponse object containing the appropriate response
        """
        try:
            response = await self.param_tree.get(path)
            status_code = 200
        except ParameterTreeError as param_error:
            response = {'error': str(param_error)}
            status_code = 400

        logging.debug("GET on path %s : %s", path, response)
        content_type = 'application/json'

        return ApiAdapterResponse(response, content_type=content_type, status_code=status_code)

    @request_types('application/json', 'application/vnd.odin-native')
    @response_types('application/json', default='application/json')
    async def put(self, path, request):
        """Handle an HTTP PUT request.

        This method handles an HTTP PUT request, decoding the request and attempting to set values
        in the asynchronous parameter tree as appropriate.

        :param path: URI path of request
        :param request: HTTP request object
        :return: an ApiAdapterResponse object containing the appropriate response
        """
        content_type = 'application/json'

        try:
            data = decode_request_body(request)
            await self.param_tree.set(path, data)
            response = await self.param_tree.get(path)
            status_code = 200
        except ParameterTreeError as param_error:
            response = {'error': str(param_error)}
            status_code = 400

        return ApiAdapterResponse(
            response, content_type=content_type, status_code=status_code
        )

    async def initialize(self, adapters):
        """Initalize the adapter.

        This dummy method demonstrates that async adapter initialisation can be performed
        asynchronously.

        :param adapters: list of adapters loaded into the server
        """
        logging.debug("SnmpAdapter initialized with %d adapters", len(adapters))
        await asyncio.sleep(0)
