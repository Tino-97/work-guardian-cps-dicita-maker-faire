import sys
import time

import cv2

import numpy as np


if not hasattr(cv2, "aruco"):
    raise ImportError(
        "Per usare la calibrazione ChArUco devi installare una build di OpenCV con il modulo aruco "
        "(ad esempio opencv-contrib-python)."
    )


ARUCO_DICT = cv2.aruco.DICT_6X6_250

SQUARES_X = 7
SQUARES_Y = 5

SQUARE_LENGTH_M = 0.046
MARKER_LENGTH_M = 0.0235

MIN_VALID_IMAGES = 15

FRAME_TIMEOUT_SEC = 5.0


def _create_charuco_board(dictionary):
    if hasattr(cv2.aruco, "CharucoBoard"):
        return cv2.aruco.CharucoBoard(
            (SQUARES_X, SQUARES_Y),
            SQUARE_LENGTH_M,
            MARKER_LENGTH_M,
            dictionary,
        )

    if hasattr(cv2.aruco, "CharucoBoard_create"):
        return cv2.aruco.CharucoBoard_create(
            SQUARES_X,
            SQUARES_Y,
            SQUARE_LENGTH_M,
            MARKER_LENGTH_M,
            dictionary,
        )

    raise RuntimeError("OpenCV non supporta ChArUco in questa installazione.")


def _create_charuco_detector(board, dictionary):
    if hasattr(cv2.aruco, "CharucoDetector"):
        return cv2.aruco.CharucoDetector(board)

    aruco_detector = None
    if hasattr(cv2.aruco, "ArucoDetector"):
        aruco_detector = cv2.aruco.ArucoDetector(dictionary)

    return {
        "board": board,
        "dictionary": dictionary,
        "aruco_detector": aruco_detector,
    }


def _detect_board(detector, board, dictionary, gray):
    if hasattr(detector, "detectBoard"):
        return detector.detectBoard(gray)

    aruco_detector = detector["aruco_detector"]
    if aruco_detector is not None:
        marker_corners, marker_ids, _ = aruco_detector.detectMarkers(gray)
    else:
        marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(gray, dictionary)

    charuco_corners = None
    charuco_ids = None

    if marker_ids is not None and len(marker_ids) > 0:
        _, charuco_corners, charuco_ids = cv2.aruco.interpolateCornersCharuco(
            marker_corners,
            marker_ids,
            gray,
            board,
        )

    return charuco_corners, charuco_ids, marker_corners, marker_ids


def _is_valid_charuco_sample(board, charuco_ids):
    if charuco_ids is None or len(charuco_ids) < 4:
        return False

    if hasattr(board, "checkCharucoCornersCollinear") and board.checkCharucoCornersCollinear(charuco_ids):
        return False

    return True


def _calibrate_charuco(all_charuco_corners, all_charuco_ids, board, image_size):
    if hasattr(cv2.aruco, "calibrateCameraCharuco"):
        return cv2.aruco.calibrateCameraCharuco(
            charucoCorners=all_charuco_corners,
            charucoIds=all_charuco_ids,
            board=board,
            imageSize=image_size,
            cameraMatrix=None,
            distCoeffs=None,
        )

    if not hasattr(board, "matchImagePoints"):
        raise RuntimeError(
            "OpenCV non espone calibrateCameraCharuco e la board non supporta matchImagePoints: "
            "fallback di calibrazione non disponibile in questa installazione."
        )

    all_object_points = []
    all_image_points = []

    for corners, ids in zip(all_charuco_corners, all_charuco_ids):
        if not _is_valid_charuco_sample(board, ids):
            continue

        obj_points, img_points = board.matchImagePoints(corners, ids)
        if obj_points is None or img_points is None:
            continue

        obj_points = np.asarray(obj_points, dtype=np.float32).reshape(-1, 1, 3)
        img_points = np.asarray(img_points, dtype=np.float32).reshape(-1, 1, 2)

        if len(obj_points) < 4 or len(img_points) < 4:
            continue

        all_object_points.append(obj_points)
        all_image_points.append(img_points)

    if not all_object_points:
        raise RuntimeError(
            "OpenCV non espone calibrateCameraCharuco e non sono stati ottenuti abbastanza punti "
            "validi per usare il fallback con calibrateCamera."
        )

    return cv2.calibrateCamera(
        objectPoints=all_object_points,
        imagePoints=all_image_points,
        imageSize=image_size,
        cameraMatrix=None,
        distCoeffs=None,
    )


def ensure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main():
    ensure_utf8_console()
    try:
        from djitellopy import Tello
    except ImportError as exc:
        raise ImportError(
            "Per usare il Tello devi installare djitellopy: pip install djitellopy"
        ) from exc

    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    board = _create_charuco_board(dictionary)
    detector = _create_charuco_detector(board, dictionary)

    tello = None
    frame_reader = None

    all_charuco_corners = []
    all_charuco_ids = []
    image_size = None
    image_size_locked = None

    try:
        tello = Tello()
        tello.connect()
        print(f"Batteria: {tello.get_battery()}%")

        try:
            tello.streamoff()
        except Exception:
            pass

        tello.streamon()
        frame_reader = tello.get_frame_read()

        frame_wait_started_at = time.monotonic()

        print("\nPremi:")
        print("  c -> salva la vista inquadrata, se la board è rilevata bene")
        print("  q -> termina l'acquisizione e avvia la calibrazione")
        print()

        while True:
            frame = None if frame_reader is None else frame_reader.frame

            if frame is None:
                if (time.monotonic() - frame_wait_started_at) >= FRAME_TIMEOUT_SEC:
                    raise TimeoutError(
                        "Nessun frame ricevuto dal Tello entro il timeout iniziale di acquisizione."
                    )
                time.sleep(0.01)
                continue

            frame_wait_started_at = time.monotonic()

            frame = frame.copy()
            display = frame.copy()

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            image_size = gray.shape[::-1]

            charuco_corners, charuco_ids, marker_corners, marker_ids = _detect_board(
                detector,
                board,
                dictionary,
                gray,
            )

            if marker_ids is not None and len(marker_ids) > 0:
                cv2.aruco.drawDetectedMarkers(display, marker_corners, marker_ids)

            if charuco_ids is not None and len(charuco_ids) > 0:
                cv2.aruco.drawDetectedCornersCharuco(display, charuco_corners, charuco_ids)

            num_corners = 0 if charuco_ids is None else len(charuco_ids)

            cv2.putText(
                display,
                f"Viste valide salvate: {len(all_charuco_corners)}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
            )

            cv2.putText(
                display,
                f"Corner ChArUco rilevati: {num_corners}",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
            )

            cv2.imshow("WORK-GUARDIAN - Calibrazione della camera", display)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("c"):
                if _is_valid_charuco_sample(board, charuco_ids):
                    if image_size_locked is None:
                        image_size_locked = image_size
                    if image_size != image_size_locked:
                        print(
                            f"Vista ignorata: la risoluzione {image_size} non coincide "
                            f"con quella delle viste già salvate {image_size_locked}."
                        )
                    else:
                        all_charuco_corners.append(charuco_corners)
                        all_charuco_ids.append(charuco_ids)
                        print(
                            f"Vista salvata con {len(charuco_ids)} corner. "
                            f"Viste valide salvate: {len(all_charuco_corners)}."
                        )
                else:
                    print("Vista non salvata: servono almeno 4 corner della board, non allineati fra loro.")

            elif key == ord("q"):
                break

        if len(all_charuco_corners) < MIN_VALID_IMAGES:
            print(f"\nViste valide insufficienti: {len(all_charuco_corners)}.")
            print(f"Per calibrare ne servono almeno {MIN_VALID_IMAGES}.")
            return

        if image_size_locked is None:
            raise RuntimeError("Nessun frame valido acquisito: calibrazione impossibile.")

        ret, camera_matrix, dist_coeffs, rvecs, tvecs = _calibrate_charuco(
            all_charuco_corners,
            all_charuco_ids,
            board,
            image_size_locked,
        )

        print("\n================ CALIBRAZIONE COMPLETATA ================")
        print(f"Errore di riproiezione (RMS): {ret:.4f} pixel")

        print("\nMatrice della camera:")
        print(camera_matrix)

        print("\nCoefficienti di distorsione:")
        print(dist_coeffs.ravel())

        print("\nDa copiare in drone/config/measurements.py:\n")

        print("CAMERA_MATRIX = (")
        print(f"    ({camera_matrix[0,0]}, {camera_matrix[0,1]}, {camera_matrix[0,2]}),")
        print(f"    ({camera_matrix[1,0]}, {camera_matrix[1,1]}, {camera_matrix[1,2]}),")
        print(f"    ({camera_matrix[2,0]}, {camera_matrix[2,1]}, {camera_matrix[2,2]}),")
        print(")")
        print()

        d = dist_coeffs.ravel()

        if len(d) < 5:
            d = np.pad(d, (0, 5 - len(d)))

        print("DIST_COEFFS = (")
        print(f"    {d[0]},")
        print(f"    {d[1]},")
        print(f"    {d[2]},")
        print(f"    {d[3]},")
        print(f"    {d[4]},")
        print(")")

    finally:
        if frame_reader is not None and hasattr(frame_reader, "stop"):
            try:
                frame_reader.stop()
            except Exception:
                pass

        if tello is not None:
            try:
                tello.streamoff()
            except Exception:
                pass

            try:
                tello.end()
            except Exception:
                pass

        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
