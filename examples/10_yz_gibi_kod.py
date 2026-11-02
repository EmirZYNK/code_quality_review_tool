# ORNEK 10: Yapay zeka tarzi kod (asiri savunmaci, gereksiz uzun isimler/yorumlar)
# Beklenen: AIP001-AIP005 (bilgi seviyesinde, olasiliksal gostergeler)

def check_if_number_is_even_or_not(input_number_value):
    """Checks whether a given number is even.

    Args:
        input_number_value: The number to check.

    Returns:
        True if even, False otherwise.
    """
    try:
        # Calculate the remainder
        remainder_of_division_result = input_number_value % 2
        # Check the remainder
        is_even_check_result = remainder_of_division_result == 0
        # Return the final result
        return is_even_check_result
    except Exception:
        return False
