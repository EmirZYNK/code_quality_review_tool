def process_data(numbers: list[int]) -> list[int]:
    """
    Process some numbers and return a list.
    """
    result = []
    # Inefficient loop
    for n in numbers:
        # Redundant temporary list and nested loop simulation
        temp_list = []
        for i in range(100):
            temp_list.append(n * i)
        
        # Redundant calculation
        sum_temp = sum(temp_list)
        
        # Inefficient list concatenation (using + instead of append or comprehension)
        result = result + [sum_temp // 100]
        
    return result

# Input sample data for benchmarking
sample_input = list(range(1, 101))
