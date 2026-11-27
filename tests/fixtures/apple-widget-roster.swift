import Foundation

@main
struct WidgetRosterTests {
    static func main() {
        let items = (0..<40).map {
            WidgetHeinzel(id: "fixture-\($0)", name: String(repeating: "Example ", count: 30),
                          state: "active", activity: "waiting_for_you", asks: nil,
                          lastApproved: nil, nextWake: nil, portrait: nil)
        }
        func testExtraLargeBoundsAndOverflow() {
            let display = WidgetRosterColumns(items: items, totalCount: 125, rowsPerColumn: 6)
            precondition(display.columns.map(\.count) == [6, 6])
            precondition(display.columns.flatMap { $0 }.map(\.id) == items.prefix(12).map(\.id))
            precondition(display.remaining == 113)
        }
        func testFallbackCountsActualVisibleRows() {
            let display = WidgetRosterColumns(items: items, totalCount: 125, rowsPerColumn: 4)
            precondition(display.columns.map(\.count) == [4, 4] && display.remaining == 117)
            let short = WidgetRosterColumns(items: Array(items.prefix(3)), totalCount: 125, rowsPerColumn: 6)
            precondition(short.columns.map(\.count) == [3] && short.remaining == 122)
        }
        func testEmptyAndInconsistentTotalsStaySafe() {
            precondition(WidgetRosterColumns(items: [], totalCount: nil, rowsPerColumn: 6).columns.isEmpty)
            for total in [nil, -1, 1] as [Int?] {
                let display = WidgetRosterColumns(items: items, totalCount: total, rowsPerColumn: 100)
                precondition(display.columns.map(\.count) == [6, 6] && display.remaining == 28)
            }
            precondition(WidgetRosterColumns(items: items, totalCount: nil, rowsPerColumn: 0)
                .columns.map(\.count) == [1, 1])
        }
        func testLowerThanDeliveredTotalAcrossFittingAlternatives() {
            for limit in [12, 8, 6, 4, 3, 2, 1] {
                let overflow = WidgetRosterOverflow(deliveredCount: items.count, totalCount: 1,
                                                   visibleCount: items.prefix(limit).count)
                precondition(overflow.total == 40 && overflow.remaining == 40 - limit)
            }
            let short = WidgetRosterOverflow(deliveredCount: 3, totalCount: 125,
                                              visibleCount: 3)
            precondition(short.total == 125 && short.remaining == 122)
        }
        testLowerThanDeliveredTotalAcrossFittingAlternatives()
        testExtraLargeBoundsAndOverflow()
        testFallbackCountsActualVisibleRows()
        testEmptyAndInconsistentTotalsStaySafe()
        print("PASS: Mac extra-large roster bounds, order, fallback and overflow")
    }
}
