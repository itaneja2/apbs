#! /usr/bin/env python

"""
Provides utility for testing apbs against examples and known results
"""

import sys
import os
import re
import datetime
import subprocess
import operator
from optparse import OptionParser
from configparser import ConfigParser, NoOptionError
from functools import reduce
from apbs_check_forces import check_forces
from apbs_check_results import check_results
from apbs_check_intermediate_energies import check_energies
from apbs_logger import Logger

# The inputgen utility needs to be accessible, so we add its path
sys.path.insert(0, "../tools/manip")
from inputgen import split_input  # noqa E402

# Matches a floating point number such as -1.23456789E-20
FLOAT_PATTERN = r"([+-]?\d+\.\d+E[+-]\d+)"


def test_binary(binary_name):
    """
    Ensures that the apbs binary is available
    """

    # Attempts to find apbs in the system path first
    try:
        binary = binary_name
        command = [binary, "--version"]
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        ) as proc:
            line = str(proc.stdout.read())
        return binary
    except OSError:
        pass

    # Next, looks for the apbs binary in the apbs bin directory
    try:
        binary = os.path.abspath(f"../build/bin/{binary_name}")
        command = [binary, "--version"]
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        ) as proc:
            line = str(proc.stdout.read())  # noqa F841
        return binary
    except OSError:
        return ""


def process_serial(binary, input_file):
    """
    Runs the apbs binary on a given input file
    """

    # First extract the name of the input file's base name
    base_name = input_file.split(".")[0]

    # The output file should have the same basename
    output_name = f"{base_name}.out"

    # Ensure that there are sufficient permissions to write to the output file
    output_file = open(output_name, "w")

    # Construct the system command and make the call
    command = [binary, input_file]
    print(f"BINARY: {binary}")
    print(f"INPUT:  {input_file}")
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as proc:
        line = str(proc.stdout.read(), "utf-8")
        sys.stdout.write(line)
        output_file.write(line)

    # Look for the results in the output file
    output_file = open(output_name, "r")
    output_text = output_file.read()

    # Look for intermidiate energy results
    output_results = check_energies(output_name)

    output_pattern = r"Global net (?:ELEC|APOL) energy \= " + FLOAT_PATTERN
    output_results2 = [
        float(r) for r in re.findall(output_pattern, output_text)
    ]

    output_results += output_results2

    # Return all the matched results as a list of floating point numbers
    return output_results


def process_parallel(binary, input_file, procs, logger):
    """
    Performs parallel apbs runs of the input file
    """

    logger.message(
        f"Splitting the input file into {procs} separate "
        + "files using the inputgen utility\n\n"
    )

    # Get the base name, and split the input file using inputgen's split_input
    base_name = input_file.split(".")[0]
    split_input(input_file)

    results = None
    for proc in range(procs):

        # Process each paralle input file and capture the results from each
        proc_input_file = f"{base_name}-PE{proc}.in"
        proc_results = process_serial(binary, proc_input_file)

        # Log the results from each parallel run
        logger.message(f"Processor {proc} results:\n")
        for proc_result in proc_results:
            logger.message(f"  {proc_result:.12e}\n")
        logger.message("\n")

        # Aggregate the results from each processor
        if results is None:
            results = proc_results
        else:
            results = [r + p for (r, p) in zip(results, proc_results)]

    # Return the aggregated results from the parllel run
    return results


def run_test(
    binary, test_files, test_name, test_directory, setup, logger, ocd
):
    """
    Runs a given test from the test cases file
    """

    logger.log("-" * 80 + "\n")
    logger.log(f"Test Timestamp: {datetime.datetime.now()}\n")
    logger.log(f"Test Name:      {test_name}\n")
    logger.log(f"Test Directory: {test_directory}\n")

    # The net time is initially zero
    net_time = datetime.timedelta(0)

    # Change the current working directory to the test directory
    os.chdir(test_directory)

    # Run the setup, if any
    if setup:
        subprocess.call(setup.split())

    for (base_name, expected_results) in test_files:

        # Get the name of the input file from the base name
        input_file = f"{base_name}.in"

        # If the expected results is 'forces', do a forces test on the input
        if expected_results == "forces":
            logger.message("-" * 80 + "\n")
            logger.message(f"Testing forces from {input_file}\n\n")
            logger.log(f"Testing forces from {input_file}\n")
            start_time = datetime.datetime.now()
            check_forces(input_file, "polarforces", "apolarforces", logger)
        else:
            logger.message("-" * 80 + "\n")
            logger.message(f"Testing input file {input_file}\n\n")
            logger.log(f"Testing {input_file}\n")

            # Record the start time before the test runs
            start_time = datetime.datetime.now()

            computed_results = None

            # Determine if this is a parallel run
            match = re.search(
                r"\s*pdime((\s+\d+)+)", open(input_file, "r").read()
            )

            # If it is parallel, get the number of procs and do a parallel run
            if match:
                procs = reduce(
                    operator.mul, [int(p) for p in match.group(1).split()]
                )
                computed_results = process_parallel(
                    binary, input_file, procs, logger
                )
            # Otherwise, just do a serial run
            else:
                computed_results = process_serial(binary, input_file)

            # Split the expected results into a list of text values
            print(f"EXPECTED COMPUTED: {len(computed_results)}")
            print(f"EXPECTED EXPECTED: {len(expected_results)}")
            print(f"COMPUTED: {computed_results}")
            print(f"EXPECTED: {expected_results}")
            expected_results = expected_results.split()
            for result in computed_results:
                print(f"COMPUTED RESULT {result}")
            for i in range(len(expected_results)):
                # If the expected result is a star, it means ignore that result
                if expected_results[i] == "*":
                    continue

                # Compare the expected to computed results
                computed_result = 0
                try:
                    computed_result = computed_results[i]
                except IndexError as error:
                    logger.message(
                        f"Computed result for index, {i}, does not "
                        + f"exist: {error}"
                    )
                expected_result = float(expected_results[i])
                logger.message(
                    "Testing computed result against "
                    + f"expected result ({computed_result:.12e}, "
                    + f"{expected_result:.12e})\n"
                )
                check_results(
                    computed_result, expected_result, input_file, logger, ocd
                )

        # Record the end time after the test
        end_time = datetime.datetime.now()
        elapsed_time = end_time - start_time
        net_time += elapsed_time
        stopwatch = elapsed_time.seconds + elapsed_time.microseconds / 1e6

        # Log the elapsed time for this test
        logger.message(f"Elapsed time: {stopwatch} seconds\n")
        logger.message("-" * 80 + "\n")

    stopwatch = net_time.seconds + net_time.microseconds / 1e6

    # Log the elapsed time for all tests that were run
    logger.message(f"Total elapsed time: {stopwatch} seconds\n")
    logger.message("Test results have been logged\n")
    logger.message("-" * 80 + "\n")
    logger.log(f"Time:           {stopwatch} seconds\n")

    os.chdir("../../tests")


def main():
    """
    Parse command line options and run tests with given options
    """

    parser = OptionParser()

    # Describes the available options.
    parser.add_option(
        "-e",
        "--executable",
        dest="executable",
        type="string",
        default="apbs",
        help="Set the apbs executable to FILE",
        metavar="FILE",
    )

    parser.add_option(
        "-c",
        "--test_config",
        dest="test_config",
        type="string",
        default="test_cases.cfg",
        help="Set the test configuration file to FILE",
        metavar="FILE",
    )

    parser.add_option(
        "-t",
        "--target_test",
        dest="target_test",
        type="string",
        action="append",
        default=[],
        help="Set the test to run to TEST",
        metavar="TEST",
    )

    parser.add_option(
        "-o",
        "--ocd",
        action="store_true",
        dest="ocd",
        help="Run APBS in OCD mode",
    )

    parser.add_option(
        "-l",
        "--log_file",
        dest="log_file",
        type="string",
        default="test.log",
        help="Save the test log to FILE.",
        metavar="FILE",
    )

    parser.add_option(
        "-b",
        "--benchmark",
        action="store_const",
        const=1,
        dest="benchmark",
        help="Perform benchmarking.",
    )

    # Parse the command line and extract option values
    (options, args) = parser.parse_args()

    # Messages will go to stdout, log messages will go to the supplied log file
    message_fd = sys.stderr
    logfile_fd = sys.stderr

    # Verify that the log file is writable
    try:
        logfile_fd = open(options.log_file, "w")
    except IOError as err:
        parser.error(
            f"Could't open log_file {options.log_file}: {err.strerror}"
        )

    # Set up the logger with the message and log file descriptor
    logger = Logger(message_fd, logfile_fd)

    # Read the test cases file
    config = ConfigParser()
    config.optionxform = str
    config.read(options.test_config)

    # Make sure that the apbs binary can be found
    binary = test_binary(options.executable)
    if binary == "":
        parser.error(
            "Coulnd't detect an apbs binary in the path or local bin directory"
        )

    # Get the names of all the test sections to run.
    test_sections = []
    if "all" in options.target_test or options.target_test == []:
        test_sections = config.sections()
        print("Testing all sections")
    else:
        test_sections = options.target_test

    print("The following sections will be tested: " + ", ".join(test_sections))
    print("=" * 80)

    # Run each test that has been requested
    for test_name in test_sections:

        print("Running tests for " + test_name + " section")

        # Verify that the test is described in the test cases file
        if test_name not in config.sections():
            print(
                "  "
                + test_name
                + " section not found in "
                + options.test_config
            )
            return 1

        # Grab the test directory
        test_directory = config.get(test_name, "input_dir")
        config.remove_option(test_name, "input_dir")

        # Check if there is a setup step.
        test_setup = None
        try:
            test_setup = config.get(test_name, "setup")
            config.remove_option(test_name, "setup")
        except NoOptionError:
            pass

        # Run the test!
        run_test(
            binary,
            config.items(test_name),
            test_name,
            test_directory,
            test_setup,
            logger,
            options.ocd,
        )

    return 0


# If this file is executed as a script, call the main function
if __name__ == "__main__":
    sys.exit(main())
