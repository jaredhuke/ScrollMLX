def calculate_tip(total_bill, tip_percentage, num_people):
    tip_amount = total_bill * (tip_percentage / 100)
    total_with_tip = total_bill + tip_amount
    amount_per_person = total_with_tip / num_people
    return tip_amount, total_with_tip, amount_per_person

if __name__ == "__main__":
    total_bill = float(input("Enter the total bill amount: $"))
    tip_percentage = float(input("Enter the tip percentage: "))
    num_people = int(input("Enter the number of people splitting the bill: "))
    tip_amount, total_with_tip, amount_per_person = calculate_tip(total_bill, tip_percentage, num_people)
    print(f"Tip amount: ${tip_amount:.2f}")
    print(f"Total amount including tip: ${total_with_tip:.2f}")
    print(f"Amount per person: ${amount_per_person:.2f}")
