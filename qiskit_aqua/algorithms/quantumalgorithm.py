# -*- coding: utf-8 -*-

# Copyright 2018 IBM.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================

"""
This module implements the abstract base class for algorithm modules.

To create add-on algorithm modules subclass the QuantumAlgorithm
class in this module.
Doing so requires that the required algorithm interface is implemented.
"""

from qiskit_aqua import Pluggable
from abc import abstractmethod
import logging
import numpy as np
import qiskit
from qiskit import __version__ as qiskit_version
from qiskit.backends import BaseBackend
from qiskit.backends.ibmq.credentials import Credentials
from qiskit.backends.ibmq.ibmqsingleprovider import IBMQSingleProvider
from qiskit_aqua import AquaError
from qiskit_aqua.utils import run_circuits
from qiskit_aqua_cmd import Preferences

logger = logging.getLogger(__name__)


class QuantumAlgorithm(Pluggable):

    # Configuration dictionary keys
    SECTION_KEY_ALGORITHM = 'algorithm'
    SECTION_KEY_OPTIMIZER = 'optimizer'
    SECTION_KEY_VAR_FORM = 'variational_form'
    SECTION_KEY_INITIAL_STATE = 'initial_state'
    SECTION_KEY_IQFT = 'iqft'
    SECTION_KEY_ORACLE = 'oracle'
    SECTION_KEY_FEATURE_MAP = 'feature_map'
    SECTION_KEY_MULTICLASS_EXTENSION = 'multiclass_extension'

    UNSUPPORTED_BACKENDS = [
        'unitary_simulator', 'clifford_simulator']

    EQUIVALENT_BACKENDS = {'statevector_simulator_py': 'statevector_simulator',
                           'statevector_simulator_sympy': 'statevector_simulator',
                           'statevector_simulator_projectq': 'statevector_simulator',
                           'qasm_simulator_py': 'qasm_simulator',
                           'qasm_simulator_projectq': 'qasm_simulator'
                           }
    """
    Base class for Algorithms.

    This method should initialize the module and its configuration, and
    use an exception if a component of the module is available.

    Args:
        configuration (dict): configuration dictionary
    """
    @abstractmethod
    def __init__(self):
        super().__init__()
        self._backend = None
        self._execute_config = {}
        self._qjob_config = {}
        self._random_seed = None
        self._random = None
        self._show_circuit_summary = False
        self._has_shared_circuits = False

    @property
    def random_seed(self):
        """Return random seed."""
        return self._random_seed

    @random_seed.setter
    def random_seed(self, seed):
        """Set random seed."""
        self._random_seed = seed

    @property
    def random(self):
        """Return a numpy random."""
        if self._random is None:
            if self._random_seed is None:
                self._random = np.random
            else:
                self._random = np.random.RandomState(self._random_seed)
        return self._random

    @property
    def backend(self):
        """Return BaseBackend backend object"""
        return self._backend

    @staticmethod
    def is_statevector_backend(backend):
        """
        Returns True if backend object is statevector.

        Args:
            backend (BaseBackend): backend instance
        Returns:
            Result (Boolean): True is statevector
        """
        return backend.configuration().backend_name.startswith('statevector') if backend is not None else False

    @staticmethod
    def backend_name(backend):
        """
        Returns backend name.

        Args:
            backend (BaseBackend):  backend instance
        Returns:
            Name (str): backend name
        """
        return backend.configuration().backend_name if backend is not None else ''

    def enable_circuit_summary(self):
        """Enable showing the summary of circuits."""
        self._show_circuit_summary = True

    def disable_circuit_summary(self):
        """Disable showing the summary of circuits."""
        self._show_circuit_summary = False

    def setup_quantum_backend(self, backend='statevector_simulator', shots=1024, skip_transpiler=False,
                              noise_params=None, coupling_map=None, initial_layout=None, hpc_params=None,
                              basis_gates=None, max_credits=10, timeout=None, wait=5):
        """
        Setup the quantum backend.

        Args:
            backend (str or BaseBackend): name of or instance of selected backend
            shots (int): number of shots for the backend
            skip_transpiler (bool): skip most of the compile steps and produce qobj directly
            noise_params (dict): the noise setting for simulator
            coupling_map (list): coupling map (perhaps custom) to target in mapping
            initial_layout (dict): initial layout of qubits in mapping
            hpc_params (dict): HPC simulator parameters
            basis_gates (str): comma-separated basis gate set to compile to
            max_credits (int): maximum credits to use
            timeout (float or None): seconds to wait for job. If None, wait indefinitely.
            wait (float): seconds between queries

        Raises:
            AquaError: set backend with invalid Qconfig
        """
        if backend is None:
            raise AquaError('Missing algorithm backend')

        if isinstance(backend, str):
            operational_backends = self.register_and_get_operational_backends()
            if QuantumAlgorithm.EQUIVALENT_BACKENDS.get(backend, backend) not in operational_backends:
                raise AquaError("This backend '{}' is not operational for the quantum algorithm, \
                                     select any one below: {}".format(backend, operational_backends))

        self._qjob_config = {'timeout': timeout,
                             'wait': wait}

        my_backend = None
        if isinstance(backend, BaseBackend):
            my_backend = backend
        else:
            try:
                my_backend = qiskit.Aer.get_backend(backend)
            except KeyError:
                preferences = Preferences()
                my_backend = qiskit.IBMQ.get_backend(backend,
                                                     url=preferences.get_url(
                                                         ''),
                                                     token=preferences.get_token(''))

            if my_backend is None:
                raise AquaError(
                    "Missing algorithm backend '{}'".format(backend))

        self._backend = my_backend

        shots = 1 if QuantumAlgorithm.is_statevector_backend(
            my_backend) else shots
        noise_params = noise_params if my_backend.configuration().simulator else None

        if my_backend.configuration().local:
            self._qjob_config.pop('wait', None)
        if coupling_map is None:
            coupling_map = my_backend.configuration().to_dict().get('coupling_map', None)
        if basis_gates is None:
            basis_gates = my_backend.configuration().basis_gates

        if isinstance(basis_gates, list):
            basis_gates = str(basis_gates)

        self._execute_config = {'shots': shots,
                                'skip_transpiler': skip_transpiler,
                                'config': {"noise_params": noise_params},
                                'basis_gates': basis_gates,
                                'coupling_map': coupling_map,
                                'initial_layout': initial_layout,
                                'max_credits': max_credits,
                                'seed': self._random_seed,
                                'qobj_id': None,
                                'hpc': hpc_params}

        info = "Algorithm: '{}' setup with backend '{}', with following setting:\n {}\n{}".format(
            self._configuration['name'], my_backend.configuration().backend_name, self._execute_config, self._qjob_config)

        logger.info('Qiskit Terra version {}'.format(qiskit_version))
        logger.info(info)

    @property
    def has_shared_circuits(self):
        return self._has_shared_circuits

    @has_shared_circuits.setter
    def has_shared_circuits(self, new_value):
        self._has_shared_circuits = new_value

    def execute(self, circuits):
        """
        A wrapper for all algorithms to interface with quantum backend.

        Args:
            circuits (QuantumCircuit or list[QuantumCircuit]): circuits to execute

        Returns:
            Result: Result object
        """
        result = run_circuits.run_circuits(circuits,
                                           self._backend,
                                           self._execute_config,
                                           self._qjob_config,
                                           self._show_circuit_summary,
                                           self.has_shared_circuits)
        if self._show_circuit_summary:
            self.disable_circuit_summary()

        return result

    @staticmethod
    def register_and_get_operational_backends():
        # update registration info using internal methods because:
        # at this point I don't want to save to or removecredentials from disk
        # I want to update url, proxies etc without removing token and
        # re-adding in 2 methods

        ibmq_backends = []
        try:
            credentials = None
            preferences = Preferences()
            url = preferences.get_url()
            token = preferences.get_token()
            if url is not None and url != '' and token is not None and token != '':
                credentials = Credentials(token,
                                          url,
                                          proxies=preferences.get_proxies({}))
            if credentials is not None:
                qiskit.IBMQ._accounts[credentials.unique_id()] = IBMQSingleProvider(
                    credentials, qiskit.IBMQ)
                logger.debug("Registered with Qiskit successfully.")
                ibmq_backends = [x.name()
                                 for x in qiskit.IBMQ.backends(url=url, token=token)]
        except Exception as e:
            logger.debug(
                "Failed to register with Qiskit: {}".format(str(e)))

        backends = set()
        aer_backends = [x.name() for x in qiskit.Aer.backends()]
        for aer_backend in aer_backends:
            backend = aer_backend
            supported = True
            for unsupported_backend in QuantumAlgorithm.UNSUPPORTED_BACKENDS:
                if backend.startswith(unsupported_backend):
                    supported = False
                    break

            if supported:
                backends.add(backend)

        return list(backends) + ibmq_backends

    @abstractmethod
    def run(self):
        pass
