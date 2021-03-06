"""Functions to establish a reference heartbeat/period
as used from prospective optical gating."""

# Module imports
import os
import numpy as np
from loguru import logger
from datetime import datetime
import j_py_sad_correlation as jps
# See comment in pyproject.toml for why we have to try both of these
try:
    import skimage.io as tiffio
except:
    import tifffile as tiffio

# Local
from . import parameters
from . import prospective_optical_gating as pog


def establish(sequence, period_history, settings, require_stable_history=True):
    # TODO: JT writes: here and elsewhere, why does this return settings back again?
    # I’m 99% sure the original settings object will be modified, so returning a new object seems confusing to me.
    # I can see that returning it could be a reminder that it is changed by the function, but equally it implies to me that
    # if the caller saved the return value into a *different* variable, the *original* object would be unmodified (which is not the case).
    # (We can't resolve this by doing a deep copy(), because the object is a large one that contains frame data.
    #  I would be wary of a shallow copy, because that's just storing up confusion for the future).
    # I will have a think and try and come up with a solution I like for this general issue.
    # -> UPDATE: actually, this is bonkers. As far as I can see, all that is updated is the reference period.
    # We should just return that value from this function, and the caller can do something with it.
    """ Attempt to establish a reference period from a sequence of recently-received frames.
        Parameters:
            sequence        list of PixelArray objects  Sequence of recently-received frame pixel arrays (in chronological order)
            period_history  list of float               Values of period calculated for previous frames (which we will append to)
            settings        dict                        Parameters controlling the sync algorithms
            require_stable_history  bool                Do we require a stable history of similar periods before we consider accepting this one?
        Returns:
            List of frame pixel arrays that form the reference sequence (or None).
    """
    start, stop, settings = establish_indices(sequence, period_history, settings, require_stable_history)
    if start is not None and stop is not None:
        referenceFrames = sequence[start:stop]
    else:
        referenceFrames = None

    return referenceFrames, settings


def establish_indices(sequence, period_history, settings, require_stable_history=True):
    """ Establish the list indices representing a reference period, from a given input sequence.
        Parameters: see header comment for establish(), above
        Returns:
            List of indices that form the reference sequence (or None).
    """
    logger.debug("Attempting to determine reference period.")
    if len(sequence) > 1:
        frame = sequence[-1]
        pastFrames = sequence[:-1]

        # Calculate Diffs between this frame and previous frames in the sequence
        diffs = jps.sad_with_references(frame, pastFrames)

        # Calculate Period based on these Diffs
        period = calculate_period_length(diffs, settings["minPeriod"], settings["lowerThresholdFactor"], settings["upperThresholdFactor"])
        if period != -1:
            period_history.append(period)

        # If we have a valid period, extract the frame indices associated with this period, and return them
        # The conditions here are empirical ones to protect against glitches where the heuristic
        # period-determination algorithm finds an anomalously short period.
        # JT TODO: The three conditions on the period history seem to be pretty similar/redundant. I wrote these many years ago,
        #  and have just left them as they "ain't broke". They should really be tidied up though.
        #  One thing I can say is that the reason for the *two* tests for >6 have to do with the fact that
        #  we are establishing the period based on looking back from the *most recent* frame, but then actually
        #  and up taking a period from a few frames earlier, since we also need to incorporate some extra padding frames.
        #  That logic could definitely be improved and tidied up - we should probably just
        #  look for a period starting numExtraRefFrames from the end of the sequence...
        # TODO: JT writes: logically these tests should probably be in calculate_period_length, rather than here
        history_stable = (len(period_history) >= (5 + (2 * settings["numExtraRefFrames"]))
                            and (len(period_history) - 1 - settings["numExtraRefFrames"]) > 0
                            and (period_history[-1 - settings["numExtraRefFrames"]]) > 6)
        if (
            period != -1
            and period > 6
            and ((require_stable_history == False) or (history_stable))
        ):
            periodToUse = period_history[-1 - settings["numExtraRefFrames"]]
            logger.success("Found a period I'm happy with: {0}".format(periodToUse))

            settings = parameters.update(
                settings, reference_period=periodToUse
            )  # automatically does referenceFrameCount an targetSyncPhase
            # DevNote: int(x+1) is the same as np.ceil(x).astype(np.int)
            numRefs = int(periodToUse + 1) + (2 * settings["numExtraRefFrames"])

            # return start, stop, settings
            logger.debug(
                "Start index: {0}; Stop index: {1};",
                len(pastFrames) - numRefs,
                len(pastFrames),
            )
            return len(pastFrames) - numRefs, len(pastFrames), settings

    logger.info("I didn't find a period I'm happy with!")
    return None, None, settings


def calculate_period_length(diffs, minPeriod=5, lowerThresholdFactor=0.5, upperThresholdFactor=0.75):
    """ Attempt to determine the period of one heartbeat, from the diffs array provided. The period will be measured backwards from the most recent frame in the array
        Parameters:
            diffs    ndarray    Diffs between latest frame and previously-received frames
        Returns:
            Period, or -1 if no period found
    """

    # Calculate the heart period (with sub-frame interpolation) based on a provided list of comparisons between the current frame and previous frames.
    bestMatchPeriod = None

    # Unlike JTs codes, the following currently only supports determining the period for a *one* beat sequence.
    # It therefore also only supports determining a period which ends with the final frame in the diffs sequence.
    if diffs.size < 2:
        logger.debug("Not enough diffs, returning -1")
        return -1

    # initialise search parameters for last diff
    score = diffs[diffs.size - 1]
    minScore = score
    maxScore = score
    totalScore = score
    meanScore = score
    minSinceMax = score
    deltaForMinSinceMax = 0
    stage = 1
    numScores = 1
    got = False

    for d in range(minPeriod, diffs.size+1):
        logger.trace(d)
        score = diffs[diffs.size - d]
        # got, values = gotScoreForDelta(score, d, values)

        totalScore += score
        numScores += 1

        lowerThresholdScore = minScore + (maxScore - minScore) * lowerThresholdFactor
        upperThresholdScore = minScore + (maxScore - minScore) * upperThresholdFactor
        logger.debug(
            "Lower Threshold:\t{0:.4f};\tUpper Threshold:\t{1:.4f}",
            lowerThresholdScore,
            upperThresholdScore,
        )

        if score < lowerThresholdScore and stage == 1:
            logger.info("Stage 1: Under lower threshold; Moving to stage 2")
            stage = 2

        if score > upperThresholdScore and stage == 2:
            # TODO: speak to JT about the 'final condition'
            logger.info(
                "Stage 2: Above upper threshold; Returning period of {0}",
                deltaForMinSinceMax,
            )
            stage = 3
            got = True
            break

        if score > maxScore:
            logger.info(
                "New max score: {0} > {1}. Resetting to stage 1.", score, maxScore
            )
            maxScore = score
            minSinceMax = score
            deltaForMinSinceMax = d
            stage = 1
        elif score != 0 and (minScore == 0 or score < minScore):
            logger.debug("New minimum score of {0}", score)
            minScore = score

        if score < minSinceMax:
            logger.debug(
                "New minimum score ({0}) since maximum of {1}", score, maxScore
            )
            minSinceMax = score
            deltaForMinSinceMax = d

        # Note this is only updated AFTER we have done the other processing (i.e. the mean score used does NOT include the current delta)
        meanScore = totalScore / numScores

    if got:
        bestMatchPeriod = deltaForMinSinceMax

    if bestMatchPeriod is None:
        logger.debug("I didn't find a whole period, returning -1")
        return -1

    bestMatchEntry = diffs.size - bestMatchPeriod

    interpolatedMatchEntry = (
        bestMatchEntry
        + pog.v_fitting(
            diffs[bestMatchEntry - 1], diffs[bestMatchEntry], diffs[bestMatchEntry + 1]
        )[0]
    )

    return diffs.size - interpolatedMatchEntry


def save_period(reference_period, parent_dir="~/"):
    """Function to save a reference period in am ISO format time-stamped folder with a parent_dir.
        Parameters:
            reference_period    ndarray     t by x by y 3d array of reference frames
            parent_dir          string      parent directory within which to store the period
    """
    dt = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    os.makedirs(os.path.join(parent_dir, dt), exist_ok=True)

    # Saves the period
    for i, frame in enumerate(reference_period):
        tiffio.imsave(os.path.join(parent_dir, dt, "{0:03d}.tiff".format(i)), frame)
